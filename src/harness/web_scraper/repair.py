"""Certified failure-only collection and immutable full-population repair views."""

import fcntl
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from harness.web_fetch.config import load_runtime_env
from harness.web_scraper.pipeline import (
    access_blocker,
    available_methods,
    digest,
    fixture_url,
    load_run,
    normalize,
    provider_call,
    qualify_fixture,
    read_json,
    redact,
    require_external_output,
    requires_fixture,
    save_json,
    score_rows,
    validate_dataset,
    validate_native_options,
    verify_retry_history,
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
            "fixture_suffix": contract.get("fixture_suffix", "")
            if contract.get("fixture_base")
            else "",
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
        "access_retries_after_repair": 1,
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


def fixture_check(home, base, cases, suffix=""):
    return [
        {key: item[key] for key in ("case_id", "url", "sha256")}
        for cid in cases
        for item in [qualify_fixture(home, base, cid, suffix=suffix, timeout=45)]
    ]


def run(home, profile, method, destination):
    require_external_output(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "execution.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (destination / "certificate.json").exists():
            contract = read_json(destination / "contract.json")
            if contract["profile_sha256"] != digest(profile.read_bytes()):
                raise ValueError("Completed repair belongs to another profile")
            participant = contract["participant"]
            if contract["method"] != method or participant["method"] != method:
                raise ValueError("Completed repair belongs to another method")
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
    frozen_inputs = read_json(home / "dataset/manifest.json")["files"]
    if (
        plan["timeout_seconds"] != 120
        or plan["calls_per_case"] != 1
        or type(plan.get("access_retries_after_repair", 1)) is not int
        or plan.get("access_retries_after_repair", 1) not in (0, 1)
    ):
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
        "access_retry_limit": plan.get("access_retries_after_repair", 1),
        "code_sha256": code,
    }
    target = destination / "contract.json"
    if target.exists() and read_json(target) != contract:
        raise ValueError("Repair resume contract changed")
    needs_fixture = requires_fixture(method, participant["native_options"])
    before = (
        fixture_check(
            home,
            plan["fixture_base"],
            participant["case_ids"],
            participant.get("fixture_suffix", ""),
        )
        if needs_fixture
        else []
    )
    if not target.exists():
        save_json(target, contract, exclusive=True)
    receipt = destination / "fixture-before.json"
    if receipt.exists():
        if read_json(receipt) != before:
            raise ValueError("Repair fixture receipt changed before dispatch")
    else:
        save_json(receipt, before, exclusive=True)
    for name, expected in code.items():
        snapshot = destination / "execution" / name
        snapshot.parent.mkdir(exist_ok=True)
        if snapshot.exists():
            if snapshot.is_symlink() or digest(snapshot.read_bytes()) != expected:
                raise ValueError("Repair execution snapshot changed before dispatch")
        else:
            snapshot.write_bytes(Path(__file__).with_name(name).read_bytes())
    journal = destination / "calls.journal.jsonl"
    entries = (
        [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines()]
        if journal.exists()
        else []
    )
    completed, histories, logical_entries = {}, {}, {}
    for entry in entries:
        row = artifact(destination, entry)
        cid = row["case_id"]
        prior = histories.setdefault(cid, [])
        if (
            cid not in participant["case_ids"]
            or row.get("physical_attempt", 1) != len(prior) + 1
            or (prior and not access_blocker(prior[-1]))
        ):
            raise ValueError("Duplicate or undeclared repaired case")
        prior.append(row)
        completed[cid] = row
        logical_entries[cid] = entry
    inflight = destination / "inflight.json"
    if inflight.exists():
        pending = read_json(inflight)
        if pending["case_id"] not in completed or completed[pending["case_id"]].get(
            "physical_attempt", 1
        ) != pending.get("physical_attempt", 1):
            raise ValueError("Ambiguous repair dispatch requires reconciliation")
        inflight.unlink()
    load_runtime_env()
    missing = [key for key in available_methods()[method]["keys"] if not os.getenv(key)]
    if missing:
        raise PermissionError("Missing credentials before dispatch: " + ", ".join(missing))
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    finished = time.monotonic() if entries else None
    for cid in participant["case_ids"]:
        if cid in completed:
            if not access_blocker(completed[cid]):
                continue
            if len(histories[cid]) > plan.get("access_retries_after_repair", 1):
                raise PermissionError("Repair access retry budget exhausted; calls retained")
        if finished is not None:
            pause = max(
                participant["minimum_call_interval_seconds"],
                participant["post_call_cooldown_seconds"],
            )
            while True:
                remaining = pause - (time.monotonic() - finished)
                if remaining <= 0:
                    break
                time.sleep(min(1, remaining))
        raw = (home / "dataset/html" / (cid + ".html")).read_bytes()
        if digest(raw) != frozen_inputs["html/" + cid + ".html"]["sha256"]:
            raise ValueError("Frozen repair HTML changed before dispatch")
        row = {
            "method": method,
            "case_id": cid,
            "input_sha256": digest(raw),
            "started_at": datetime.now(UTC).isoformat(),
            "kind": "failure_repair",
            "physical_attempt": len(histories.get(cid, [])) + 1,
        }
        save_json(inflight, row, exclusive=True)
        started = time.monotonic()
        try:
            row.update(
                provider_call(
                    method,
                    raw.decode(),
                    truth[cid]["url"],
                    fixture_url(plan["fixture_base"], cid, participant.get("fixture_suffix", ""))
                    if needs_fixture
                    else None,
                    home=home,
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
        prior_calls = histories.get(cid, [])
        if prior_calls:
            row["physical_attempts"] = prior_calls + [dict(row)]
            row["retry_execution_seconds"] = sum(r.get("elapsed_seconds") or 0 for r in prior_calls)
            row["retry_missing_timers"] = sum(r.get("elapsed_seconds") is None for r in prior_calls)
        suffix = "" if row["physical_attempt"] == 1 else "-attempt" + str(row["physical_attempt"])
        path = destination / "calls" / (cid + suffix + ".json")
        save_json(path, redact(row), exclusive=True)
        entry = {
            "path": path.relative_to(destination).as_posix(),
            "sha256": digest(path.read_bytes()),
        }
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        inflight.unlink()
        entries.append(entry)
        completed[cid] = row
        logical_entries[cid] = entry
        histories.setdefault(cid, []).append(row)
        finished = time.monotonic()
        print(method, cid[:10], row["compatibility"], row.get("http_status"), flush=True)
        if access_blocker(row):
            raise PermissionError("Repair stopped on native access denial")
    after = (
        fixture_check(
            home,
            plan["fixture_base"],
            participant["case_ids"],
            participant.get("fixture_suffix", ""),
        )
        if needs_fixture
        else []
    )
    if before != after:
        raise ValueError("Repair fixtures changed")
    validate_dataset(home)
    if plan["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Repair dataset changed during collection")
    if digest(profile.read_bytes()) != contract["profile_sha256"]:
        raise ValueError("Repair profile changed during collection")
    verify_parent(home, participant)
    if code != {n: digest(Path(__file__).with_name(n).read_bytes()) for n in code}:
        raise ValueError("Repair implementation changed during calls")
    save_json(destination / "fixture-after.json", after)
    manifest = {
        "contract_sha256": digest(target.read_bytes()),
        "calls": [logical_entries[cid] for cid in participant["case_ids"]],
        "physical_calls": entries,
        "fixtures": {
            name: digest((destination / name).read_bytes())
            for name in ("fixture-before.json", "fixture-after.json")
        },
    }
    save_json(destination / "manifest.json", manifest)
    certificate = {
        "schema": "failed-extraction-repair-certificate/1",
        "profile_sha256": contract["profile_sha256"],
        "manifest_sha256": digest((destination / "manifest.json").read_bytes()),
        "case_count": len(completed),
        "method": method,
    }
    save_json(destination / "certificate.json", certificate)
    return {"method": method, "repaired_calls": len(completed), "physical_calls": len(entries)}


def compose(home, source, rows, repair):
    """Overlay only declared failed calls; keep old calls and missing timers explicit."""
    directory = Path(repair["path"])
    certificate = read_json(directory / "certificate.json")
    if digest((directory / "certificate.json").read_bytes()) != repair["sha256"]:
        raise ValueError("Repair certificate changed")
    manifest = read_json(directory / "manifest.json")
    contract = read_json(directory / "contract.json")
    participant = contract["participant"]
    if (
        certificate["profile_sha256"] != contract["profile_sha256"]
        or certificate["method"] != contract["method"]
        or certificate["method"] != participant["method"]
        or certificate["case_count"] != len(manifest["calls"])
    ):
        raise ValueError("Repair certificate and frozen contract identities differ")
    if Path(participant["parent_run"]).resolve() != source.resolve():
        raise ValueError("Repair belongs to another parent")
    _, predecessor = verify_parent(home, participant)

    def predecessor_identity(row):
        # Aggregation may attach physical call inventories; native logical calls stay identical.
        derived = {
            "physical_attempts",
            "normalized_fields",
            "collected_normalized_fields",
            "projection_revision",
        }
        value = {k: v for k, v in row.items() if k not in derived}
        if "superseded_result" in value:
            value["superseded_result"] = predecessor_identity(value["superseded_result"])
        return value

    if [predecessor_identity(r) for r in rows] != [predecessor_identity(r) for r in predecessor]:
        raise ValueError("Repair predecessor chain differs from current results")
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
    needs_fixture = requires_fixture(participant["method"], participant["native_options"])
    if needs_fixture:
        if {item["case_id"] for item in before} != set(participant["case_ids"]):
            raise ValueError("Repair fixture qualification is incomplete")
        for item in before:
            raw = home / "dataset/html" / (item["case_id"] + ".html")
            if item["sha256"] != digest(raw.read_bytes()):
                raise ValueError("Repair fixture qualification source changed")
    verify_retry_history(directory, manifest, contract)
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
        if row.get("compatibility") == "structured_output":
            raise ValueError("Repair cannot replace a current successful result")
        previous = row.get("physical_attempts") or [row]
        retry_seconds = (row.get("retry_execution_seconds") or 0) + (
            row.get("elapsed_seconds") or 0
        )
        retry_missing = row.get("retry_missing_timers", 0) + (row.get("elapsed_seconds") is None)
        result.append(
            {
                **replacement,
                "repair_run_id": directory.name,
                "physical_attempts": previous
                + (replacement.get("physical_attempts") or [replacement]),
                "superseded_result": row,
                "retry_execution_seconds": retry_seconds
                + (replacement.get("retry_execution_seconds") or 0),
                "retry_missing_timers": retry_missing + replacement.get("retry_missing_timers", 0),
            }
        )
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    return result, score_rows(home, truth, result, [certificate["method"]])
