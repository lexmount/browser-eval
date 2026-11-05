"""Certified failure-only collection and immutable full-population repair views."""

import fcntl
import re
from pathlib import Path

from harness.web_extract.dataset import task_bindings
from harness.web_extract.pipeline import (
    available_methods,
    collect_calls,
    digest,
    execution_identity,
    load_run,
    qualify_fixture,
    read_json,
    require_external_output,
    requires_fixture,
    resolve_run_path,
    save_json,
    validate_dataset,
    validate_fixture_base,
    validate_native_options,
    verify_retry_history,
)
from harness.web_fetch.config import load_runtime_env


def create_plan(home, content, output, options, fixture_base, selected=None):
    require_external_output(output)
    require_external_output(home)
    home = Path(home).resolve()
    participants = {}
    for selection in read_json(content)["evidence_runs"]:
        source, rows = load_selection(home, selection)
        contract = read_json(source / "contract.json")
        method = contract["methods"][0]
        previous = selection.get("repair", [])
        failures = [r["case_id"] for r in rows if r.get("compatibility") != "structured_output"]
        if selected is not None and method not in selected:
            continue
        cases = selected.get(method, []) if selected is not None else failures
        if not set(cases).issubset(failures) or len(cases) != len(set(cases)):
            raise ValueError("Repair selection includes successful or duplicate cases")
        if not cases:
            continue
        if contract.get("snapshot_manifest"):
            bound_base = contract["snapshot_manifest"]["fixture_base"]
            if fixture_base is not None and fixture_base != bound_base:
                raise ValueError("Cannot change the frozen dataset snapshot during repair")
            fixture_base = bound_base
        cert = read_json(source / "certificate.json")
        if digest((source / "manifest.json").read_bytes()) != cert["manifest_sha256"]:
            raise ValueError("Repair parent certificate differs")
        participants[method] = {
            "method": method,
            **(
                {"snapshot_manifest": contract["snapshot_manifest"]}
                if contract.get("snapshot_manifest")
                else {}
            ),
            "parent_run": str(source.relative_to(home))
            if source.is_relative_to(home)
            else str(source),
            "parent_manifest_sha256": cert["manifest_sha256"],
            "parent_certificate_sha256": digest((source / "certificate.json").read_bytes()),
            "case_ids": cases,
            "failed_case_ids": failures,
            "previous_repairs": previous,
            "native_options": validate_native_options(
                method, {**contract.get("native_options", {}), **options.get(method, {})}
            ),
            "fixture_suffix": contract.get("fixture_suffix", "")
            if contract.get("fixture_base")
            else ".html",
            "minimum_call_interval_seconds": available_methods()[method][
                "minimum_call_interval_seconds"
            ],
            "post_call_cooldown_seconds": available_methods()[method]["post_call_cooldown_seconds"],
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
        **task_bindings(home),
    }
    if fixture_base is not None:
        validate_fixture_base(fixture_base)
    save_json(output, plan, exclusive=True)
    return plan


def verify_parent(home, participant):
    require_external_output(home)
    home = Path(home).resolve()
    source = home / participant["parent_run"]
    if digest((source / "manifest.json").read_bytes()) != participant["parent_manifest_sha256"]:
        raise ValueError("Repair parent manifest changed")
    if (
        digest((source / "certificate.json").read_bytes())
        != participant["parent_certificate_sha256"]
    ):
        raise ValueError("Repair parent certificate changed")
    _, rows = load_selection(
        home, {"run": str(source), "repair": participant.get("previous_repairs", [])}
    )
    contract = read_json(source / "contract.json")
    if contract["methods"] != [participant["method"]]:
        raise ValueError("Repair parent method differs")
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


def fixture_check(home, base, cases, suffix="", snapshot_manifest=None):
    if snapshot_manifest is not None:
        from harness.web_extract.snapshots import verify_bound_manifest

        return verify_bound_manifest(home, snapshot_manifest, base)
    return [
        {key: item[key] for key in ("case_id", "url", "sha256")}
        for cid in cases
        for item in [qualify_fixture(home, base, cid, suffix=suffix, timeout=45)]
    ]


def run(home, profile, method, destination):
    require_external_output(destination)
    require_external_output(home)
    home = Path(home).resolve()
    for name in (
        "execution.lock",
        "calls",
        "calls.journal.jsonl",
        "contract.json",
        "inflight.json",
        "manifest.json",
        "fixture-before.json",
        "fixture-after.json",
        "execution/pipeline.py",
        "execution/repair.py",
    ):
        require_external_output(destination / name)
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
                home / participant["parent_run"],
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
    if plan.get("fixture_base") is not None:
        validate_fixture_base(plan["fixture_base"])
    bindings = task_bindings(home)
    if any(plan.get(key) != value for key, value in bindings.items()):
        raise ValueError("Frozen repair task adapter or dataset profile changed")
    participant = plan["participants"][method]
    if participant["method"] != method:
        raise ValueError("Repair participant method differs")
    validate_native_options(method, participant["native_options"])
    verify_parent(home, participant)
    if (
        plan["timeout_seconds"] != 120
        or plan["calls_per_case"] != 1
        or type(plan.get("access_retries_after_repair", 1)) is not int
        or plan.get("access_retries_after_repair", 1) != 1
    ):
        raise ValueError("Unsupported repair execution budget")
    if plan["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Repair dataset changed")
    code = {
        name: digest(Path(__file__).with_name(name).read_bytes())
        for name in ("repair.py", "pipeline.py")
    }
    contract = {
        "schema": "failed-extraction-repair-run/2"
        if bindings
        else "failed-extraction-repair-run/1",
        "method": method,
        "profile_sha256": digest(profile.read_bytes()),
        "participant": participant,
        "access_retry_limit": plan.get("access_retries_after_repair", 1),
        "code_sha256": code,
        "code_snapshots": False,
        **bindings,
    }
    target = destination / "contract.json"
    if not target.exists() and (
        any((destination / name).exists() for name in ("calls.journal.jsonl", "inflight.json"))
        or any((destination / "calls").glob("*"))
    ):
        raise ValueError("Repair contract is absent from an already-started execution")
    if target.exists() and read_json(target) != contract:
        raise ValueError("Repair resume contract changed")
    needs_fixture = requires_fixture(method, participant["native_options"])
    before = (
        fixture_check(
            home,
            plan["fixture_base"],
            participant["case_ids"],
            participant.get("fixture_suffix", ""),
            snapshot_manifest=participant.get("snapshot_manifest"),
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
    load_runtime_env()
    entries_by_case, entries = collect_calls(
        home,
        [method],
        participant["case_ids"],
        destination,
        contract,
        fixture_base=plan["fixture_base"] if needs_fixture else None,
        fixture_suffix=participant.get("fixture_suffix", ""),
        min_call_interval=participant["minimum_call_interval_seconds"],
        post_call_cooldown=participant["post_call_cooldown_seconds"],
        native_options=participant["native_options"],
        repair=True,
    )
    logical_entries = {read_json(destination / e["path"])["case_id"]: e for e in entries_by_case}
    after = (
        fixture_check(
            home,
            plan["fixture_base"],
            participant["case_ids"],
            participant.get("fixture_suffix", ""),
            snapshot_manifest=participant.get("snapshot_manifest"),
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
    if task_bindings(home) != bindings:
        raise ValueError("Repair task adapter or dataset profile changed during calls")
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
        "case_count": len(logical_entries),
        "method": method,
        **bindings,
    }
    save_json(destination / "certificate.json", certificate)
    return {
        "method": method,
        "repaired_calls": len(logical_entries),
        "physical_calls": len(entries),
    }


def verify_repair(home, source, repair):
    """Overlay only declared failed calls; keep old calls and missing timers explicit."""
    directory = Path(repair["path"])
    certificate = read_json(directory / "certificate.json")
    if digest((directory / "certificate.json").read_bytes()) != repair["sha256"]:
        raise ValueError("Repair certificate changed")
    manifest = read_json(directory / "manifest.json")
    contract = read_json(directory / "contract.json")
    if contract.get("schema") == "failed-extraction-repair-run/2":
        for key in ("task_adapter_sha256", "dataset_profile_sha256", "dataset_registry_sha256"):
            value = contract.get(key)
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
                or certificate.get(key) != value
            ):
                raise ValueError(
                    "Repair task adapter or dataset binding differs from certification"
                )
    participant = contract["participant"]
    if (
        certificate["profile_sha256"] != contract["profile_sha256"]
        or certificate["method"] != contract["method"]
        or certificate["method"] != participant["method"]
        or certificate["case_count"] != len(manifest["calls"])
    ):
        raise ValueError("Repair certificate and frozen contract identities differ")
    if (
        digest((source / "manifest.json").read_bytes()) != participant["parent_manifest_sha256"]
        or digest((source / "certificate.json").read_bytes())
        != participant["parent_certificate_sha256"]
    ):
        raise ValueError("Repair belongs to another parent")
    if digest((directory / "manifest.json").read_bytes()) != certificate["manifest_sha256"]:
        raise ValueError("Repair manifest changed")
    if digest((directory / "contract.json").read_bytes()) != manifest["contract_sha256"]:
        raise ValueError("Repair contract changed")
    for name, expected in contract["code_sha256"].items():
        if name not in ("repair.py", "pipeline.py"):
            raise ValueError("Unexpected repair execution source")
        snapshot = directory / "execution" / name
        if contract.get("code_snapshots", True) and (
            snapshot.is_symlink() or digest(snapshot.read_bytes()) != expected
        ):
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
    if needs_fixture and participant.get("snapshot_manifest"):
        from harness.web_extract.snapshots import manifest_receipt

        if before != manifest_receipt(
            home, participant["snapshot_manifest"], participant["snapshot_manifest"]["fixture_base"]
        ):
            raise ValueError("Repair dataset snapshot qualification differs")
    elif needs_fixture:
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
    return participant, replacements, certificate, directory


def overlay_repair(rows, replacements, directory):
    """Select repaired failures without rereading or rescoring the verified chain."""
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
    return result


def load_selection(home, selection):
    """Verify each selected run and repair once, then return its current logical rows."""
    require_external_output(home)
    home = Path(home).resolve()
    source = resolve_run_path(home, selection["run"])
    require_external_output(source)
    source = source.resolve()
    _, rows = load_run(source)
    manifest = read_json(source / "manifest.json")
    attempts = {}
    for entry in manifest.get("physical_calls", manifest["calls"]):
        row = artifact(source, entry)
        attempts.setdefault((row["method"], row["case_id"]), []).append(row)
    # A single retained call is already the complete history. Enrich only retries,
    # so adding history does not change the identity of superseded single calls.
    rows = [
        {**row, "physical_attempts": history} if len(history) > 1 else row
        for row in rows
        for history in [attempts[(row["method"], row["case_id"])]]
    ]
    chain = []
    for revision in selection.get("repair", []):
        participant, replacements, _, directory = verify_repair(home, source, revision)
        if [r["sha256"] for r in participant.get("previous_repairs", [])] != chain:
            raise ValueError("Repair predecessor chain differs from current results")
        rows = overlay_repair(rows, replacements, directory)
        chain.append(revision["sha256"])
    return source, rows


def compose(home, source, rows, repair):
    """Overlay one repair only when its exact predecessor matches the supplied rows."""
    require_external_output(home)
    require_external_output(source)
    home, source = Path(home).resolve(), Path(source).resolve()
    participant, replacements, _, directory = verify_repair(home, source, repair)
    _, predecessor = load_selection(
        home, {"run": str(source), "repair": participant.get("previous_repairs", [])}
    )
    if [execution_identity(r) for r in rows] != [execution_identity(r) for r in predecessor]:
        raise ValueError("Repair predecessor chain differs from current results")
    return overlay_repair(rows, replacements, directory)
