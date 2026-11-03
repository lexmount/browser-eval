"""Certified failure-only collection and immutable full-population repair views."""

import fcntl
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from harness.web_fetch.config import load_runtime_env
from harness.web_fetch.providers.http_client import request
from harness.web_scraper.pipeline import (
    access_blocker,
    digest,
    load_run,
    normalize,
    provider_call,
    read_json,
    redact,
    require_external_output,
    save_json,
    score_rows,
    validate_dataset,
    validate_native_options,
)


def create_plan(home, content, output, options, fixture_base, selected=None):
    require_external_output(output)
    participants = {}
    for selection in read_json(content)["evidence_runs"]:
        source = Path(selection["run"])
        source = source if source.is_absolute() else home / "runs" / source
        contract, rows = load_run(source)
        method = contract["methods"][0]
        previous = selection.get("repair", [])
        previous = previous if isinstance(previous, list) else [previous]
        for revision in previous:
            rows, _ = compose(home, source, rows, revision)
        failures = [r["case_id"] for r in rows if r.get("compatibility") != "structured_output"]
        if selected is not None and method not in selected:
            continue
        cases = selected.get(method, []) if selected is not None else failures
        if not set(cases).issubset(failures) or len(cases) != len(set(cases)):
            raise ValueError("Repair selection includes successful or duplicate cases")
        if not cases:
            continue
        cert = read_json(source / "certificate.json")
        if digest((source / "manifest.json").read_bytes()) != cert["manifest_sha256"]:
            raise ValueError("Repair parent certificate differs")
        participants[method] = {
            "method": method,
            "parent_run": str(source),
            "parent_manifest_sha256": cert["manifest_sha256"],
            "parent_certificate_sha256": digest((source / "certificate.json").read_bytes()),
            "case_ids": cases,
            "failed_case_ids": failures,
            "previous_repairs": previous,
            "native_options": validate_native_options(method, options.get(method, {})),
            "minimum_call_interval_seconds": 13 if method == "diffbot-product" else 0,
            "post_call_cooldown_seconds": 10 if method == "diffbot-product" else 0,
        }
    plan = {
        "schema": "archived-extraction-repair/1",
        "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
        "participants": participants,
        "fixture_base": fixture_base,
        "timeout_seconds": 120,
        "calls_per_case": 1,
        "selection": "All original non-structured results; never replace successful originals",
    }
    save_json(output, plan, exclusive=True)
    return plan


def verify_parent(home, participant):
    source = Path(participant["parent_run"])
    if digest((source / "manifest.json").read_bytes()) != participant["parent_manifest_sha256"]:
        raise ValueError("Repair parent manifest changed")
    if (
        digest((source / "certificate.json").read_bytes())
        != participant["parent_certificate_sha256"]
    ):
        raise ValueError("Repair parent certificate changed")
    contract, rows = load_run(source)
    if contract["methods"] != [participant["method"]]:
        raise ValueError("Repair parent method differs")
    for revision in participant.get("previous_repairs", []):
        rows, _ = compose(home, source, rows, revision)
    chosen = participant["case_ids"]
    failures = {r["case_id"] for r in rows if r.get("compatibility") != "structured_output"}
    if (
        len(chosen) != len(set(chosen))
        or not set(chosen).issubset(failures)
        or set(participant.get("failed_case_ids", chosen)) != failures
    ):
        raise ValueError("Repair must select declared failures, never successes")
    return contract, rows


def artifact(directory, entry):
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe repair evidence path")
    path = directory / relative
    if digest(path.read_bytes()) != entry["sha256"]:
        raise ValueError("Repair evidence changed")
    return read_json(path)


def fixture_check(home, base, cases):
    if not base or not base.startswith("https://"):
        raise ValueError("Repair URL input requires frozen HTTPS fixtures")
    result = []
    for cid in cases:
        url = base.rstrip("/") + "/" + cid
        response = request(url, timeout=45, max_bytes=3_000_000)
        expected = digest((home / "dataset/html" / (cid + ".html")).read_bytes())
        if response.status != 200 or response.url != url or digest(response.body) != expected:
            raise ValueError("Repair fixture bytes or canonical URL differ")
        result.append({"case_id": cid, "url": url, "sha256": expected})
    return result


def run(home, profile, method, destination):
    require_external_output(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (destination / "certificate.json").exists():
            contract = read_json(destination / "contract.json")
            if contract["profile_sha256"] != digest(profile.read_bytes()):
                raise ValueError("Completed repair belongs to another profile")
            participant = contract["participant"]
            _, rows = verify_parent(home, participant)
            compose(
                home,
                Path(participant["parent_run"]),
                rows,
                {
                    "path": str(destination),
                    "sha256": digest((destination / "certificate.json").read_bytes()),
                },
            )
            return {"method": method, "repaired_calls": len(participant["case_ids"])}
        return collect(home, profile, method, destination)


def collect(home, profile, method, destination):
    validate_dataset(home)
    plan = read_json(profile)
    participant = plan["participants"][method]
    if participant["method"] != method:
        raise ValueError("Repair participant method differs")
    validate_native_options(method, participant["native_options"])
    verify_parent(home, participant)
    if plan["timeout_seconds"] != 120 or plan["calls_per_case"] != 1:
        raise ValueError("Unsupported repair execution budget")
    if plan["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Repair dataset changed")
    code = {
        name: digest(Path(__file__).with_name(name).read_bytes())
        for name in ("repair.py", "pipeline.py")
    }
    contract = {
        "schema": "failed-extraction-repair-run/1",
        "method": method,
        "profile_sha256": digest(profile.read_bytes()),
        "participant": participant,
        "code_sha256": code,
    }
    target = destination / "contract.json"
    if target.exists() and read_json(target) != contract:
        raise ValueError("Repair resume contract changed")
    needs_fixture = method in ("oxylabs-json", "firecrawl-json", "browserbase-json") or (
        participant["native_options"].get("use_fixture_context")
        or participant["native_options"].get("input_route") == "url"
    )
    before = (
        fixture_check(home, plan["fixture_base"], participant["case_ids"]) if needs_fixture else []
    )
    if not target.exists():
        save_json(target, contract, exclusive=True)
        save_json(destination / "fixture-before.json", before, exclusive=True)
        for name in code:
            p = destination / "execution" / name
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(Path(__file__).with_name(name).read_bytes())
    journal = destination / "calls.journal.jsonl"
    entries = [json.loads(x) for x in journal.read_text().splitlines()] if journal.exists() else []
    completed = {}
    for entry in entries:
        row = artifact(destination, entry)
        if row["case_id"] in completed or row["case_id"] not in participant["case_ids"]:
            raise ValueError("Duplicate or undeclared repaired case")
        completed[row["case_id"]] = row
    inflight = destination / "inflight.json"
    if inflight.exists():
        pending = read_json(inflight)
        if pending["case_id"] not in completed:
            raise ValueError("Ambiguous repair dispatch requires reconciliation")
        inflight.unlink()
    load_runtime_env()
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    finished = time.monotonic() if entries else None
    for cid in participant["case_ids"]:
        if cid in completed:
            continue
        if finished is not None:
            pause = max(
                participant["minimum_call_interval_seconds"],
                participant["post_call_cooldown_seconds"],
            )
            while time.monotonic() - finished < pause:
                time.sleep(min(1, pause - (time.monotonic() - finished)))
        raw = (home / "dataset/html" / (cid + ".html")).read_bytes()
        row = {
            "method": method,
            "case_id": cid,
            "input_sha256": digest(raw),
            "started_at": datetime.now(UTC).isoformat(),
            "kind": "failure_repair",
        }
        save_json(inflight, row, exclusive=True)
        started = time.monotonic()
        try:
            row.update(
                provider_call(
                    method,
                    raw.decode(),
                    truth[cid]["url"],
                    plan["fixture_base"].rstrip("/") + "/" + cid if needs_fixture else None,
                    native_options=participant["native_options"],
                    timeout=120,
                )
            )
            row["normalized_fields"] = normalize(method, row.get("response", {}))
            row["compatibility"] = (
                "structured_output"
                if row.get("http_status") == 200 and row["normalized_fields"] is not None
                else "no_structured_output"
            )
        except Exception as error:
            row.update(
                state="call_error",
                error=redact(str(error)),
                normalized_fields=None,
                compatibility="no_structured_output",
            )
        row["elapsed_seconds"] = time.monotonic() - started
        row["finished_at"] = datetime.now(UTC).isoformat()
        path = destination / "calls" / (cid + ".json")
        save_json(path, redact(row), exclusive=True)
        entry = {
            "path": path.relative_to(destination).as_posix(),
            "sha256": digest(path.read_bytes()),
        }
        with journal.open("a") as stream:
            stream.write(json.dumps(entry) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        inflight.unlink()
        entries.append(entry)
        completed[cid] = row
        finished = time.monotonic()
        print(method, cid[:10], row["compatibility"], row.get("http_status"), flush=True)
        if access_blocker(row):
            raise PermissionError("Repair stopped on native access denial")
    after = (
        fixture_check(home, plan["fixture_base"], participant["case_ids"]) if needs_fixture else []
    )
    if before != after:
        raise ValueError("Repair fixtures changed")
    verify_parent(home, participant)
    if code != {n: digest(Path(__file__).with_name(n).read_bytes()) for n in code}:
        raise ValueError("Repair implementation changed during calls")
    save_json(destination / "fixture-after.json", after)
    manifest = {
        "contract_sha256": digest(target.read_bytes()),
        "calls": entries,
        "fixtures": {
            name: digest((destination / name).read_bytes())
            for name in ("fixture-before.json", "fixture-after.json")
        },
    }
    save_json(destination / "manifest.json", manifest)
    certificate = {
        "schema": "failed-extraction-repair-certificate/1",
        "profile_sha256": digest(profile.read_bytes()),
        "manifest_sha256": digest((destination / "manifest.json").read_bytes()),
        "case_count": len(entries),
        "method": method,
    }
    save_json(destination / "certificate.json", certificate)
    return {"method": method, "repaired_calls": len(entries)}


def compose(home, source, rows, repair):
    """Overlay only declared failed calls; keep old calls and missing timers explicit."""
    directory = Path(repair["path"])
    certificate = read_json(directory / "certificate.json")
    if digest((directory / "certificate.json").read_bytes()) != repair["sha256"]:
        raise ValueError("Repair certificate changed")
    manifest = read_json(directory / "manifest.json")
    contract = read_json(directory / "contract.json")
    participant = contract["participant"]
    if Path(participant["parent_run"]).resolve() != source.resolve():
        raise ValueError("Repair belongs to another parent")
    verify_parent(home, participant)
    if digest((directory / "manifest.json").read_bytes()) != certificate["manifest_sha256"]:
        raise ValueError("Repair manifest changed")
    if digest((directory / "contract.json").read_bytes()) != manifest["contract_sha256"]:
        raise ValueError("Repair contract changed")
    for name, expected in contract["code_sha256"].items():
        if name not in ("repair.py", "pipeline.py"):
            raise ValueError("Unexpected repair execution source")
        snapshot = directory / "execution" / name
        if snapshot.is_symlink() or digest(snapshot.read_bytes()) != expected:
            raise ValueError("Repair execution source changed")
    for name, expected in manifest.get("fixtures", {}).items():
        if name not in ("fixture-before.json", "fixture-after.json"):
            raise ValueError("Unexpected repair fixture receipt")
        if digest((directory / name).read_bytes()) != expected:
            raise ValueError("Repair fixture receipt changed")
    before = read_json(directory / "fixture-before.json")
    after = read_json(directory / "fixture-after.json")
    if before != after:
        raise ValueError("Repair fixture qualification differs")
    needs_fixture = (
        participant["native_options"].get("use_fixture_context")
        or (participant["native_options"].get("input_route") == "url")
        or participant["method"] in ("oxylabs-json", "firecrawl-json", "browserbase-json")
    )
    if needs_fixture:
        if {item["case_id"] for item in before} != set(participant["case_ids"]):
            raise ValueError("Repair fixture qualification is incomplete")
        for item in before:
            raw = home / "dataset/html" / (item["case_id"] + ".html")
            if item["sha256"] != digest(raw.read_bytes()):
                raise ValueError("Repair fixture qualification source changed")
    replacements = {}
    for entry in manifest["calls"]:
        row = artifact(directory, entry)
        if row["method"] != certificate["method"] or row["method"] != participant["method"]:
            raise ValueError("Repaired response belongs to another method")
        if row["input_sha256"] != digest(
            (home / "dataset/html" / (row["case_id"] + ".html")).read_bytes()
        ):
            raise ValueError("Repaired source HTML changed")
        replacements[row["case_id"]] = row
    if set(replacements) != set(participant["case_ids"]) or len(manifest["calls"]) != len(
        replacements
    ):
        raise ValueError("Repair scope is incomplete or duplicated")
    result = []
    for row in rows:
        replacement = replacements.get(row["case_id"])
        if replacement is None:
            result.append(row)
            continue
        previous = row.get("physical_attempts") or [row]
        result.append(
            {
                **replacement,
                "repair_run_id": directory.name,
                "physical_attempts": previous + [replacement],
                "superseded_result": row,
                "retry_execution_seconds": sum(x.get("elapsed_seconds") or 0 for x in previous),
                "retry_missing_timers": sum(x.get("elapsed_seconds") is None for x in previous),
            }
        )
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    return result, score_rows(home, truth, result, [certificate["method"]])
