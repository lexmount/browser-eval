"""Certified failure-only collection and immutable full-population repair views."""

import fcntl
import os
import re
from pathlib import Path

from harness.benchmark_execution import ExecutionPolicy, exclusive_policy
from harness.reporting.publication import atomic_create
from harness.web_extract.dataset import task_bindings
from harness.web_extract.pipeline import (
    ACCESS_POLICY,
    RECORD_INSTRUCTION_DELIVERY,
    RECORD_INSTRUCTIONS,
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


def require_archived(home):
    if input_mode(home) == "live":
        raise ValueError(
            "Live URL cohorts must resume their original certified run, not an archived repair"
        )


def create_plan(home, content, output, options, fixture_base, selected=None):
    require_archived(home)
    require_external_output(output)
    require_external_output(home)
    home = Path(home).resolve()
    participants = {}
    seen = set()
    for selection in read_json(content)["evidence_runs"]:
        source, rows, configurations = load_selection(home, selection, with_configurations=True)
        contract = read_json(source / "contract.json")
        methods = contract.get("methods")
        if not isinstance(methods, list) or len(methods) != 1:
            raise ValueError("Each repair parent must declare exactly one method")
        method = methods[0]
        if method in seen:
            raise ValueError("Repair selections contain duplicate methods")
        seen.add(method)
        if selected is not None and method not in selected:
            continue
        if (
            task_bindings(home)
            and contract.get("extraction_instruction_delivery") != RECORD_INSTRUCTION_DELIVERY
        ):
            raise ValueError(
                "Historical instruction delivery differs; use a new cohort, not a repair"
            )
        previous = selection.get("repair", [])
        failures = [r["case_id"] for r in rows if r.get("compatibility") != "structured_output"]
        cases = selected.get(method, []) if selected is not None else failures
        if not set(cases).issubset(failures) or len(cases) != len(set(cases)):
            raise ValueError("Repair selection includes successful or duplicate cases")
        if not cases:
            continue
        inherited = configurations[cases[0]]
        if any(configurations[cid] != inherited for cid in cases):
            raise ValueError(
                "Selected failures have different execution settings; select a uniform subset"
            )
        native_options = validate_native_options(
            method, {**inherited["native_options"], **options.get(method, {})}
        )
        if requires_fixture(method, native_options):
            if fixture_base is None:
                fixture_base = inherited["fixture_base"]
            elif inherited["fixture_base"] and fixture_base != inherited["fixture_base"]:
                raise ValueError("Cannot change the frozen dataset snapshot during repair")
        if requires_fixture(method, native_options) and fixture_base is None:
            raise ValueError("URL repair requires a frozen fixture origin")
        if inherited.get("snapshot_manifest"):
            bound_base = inherited["snapshot_manifest"]["fixture_base"]
            if fixture_base is not None and fixture_base != bound_base:
                raise ValueError("Cannot change the frozen dataset snapshot during repair")
            fixture_base = bound_base
        cert = read_json(source / "certificate.json")
        if digest((source / "manifest.json").read_bytes()) != cert["manifest_sha256"]:
            raise ValueError("Repair parent certificate differs")
        participants[method] = {
            "method": method,
            **(
                {
                    "extraction_instructions": contract.get(
                        "extraction_instructions", RECORD_INSTRUCTIONS
                    )
                }
                if task_bindings(home)
                else {}
            ),
            **(
                {"extraction_instruction_delivery": contract["extraction_instruction_delivery"]}
                if task_bindings(home) and "extraction_instruction_delivery" in contract
                else {}
            ),
            **(
                {
                    "snapshot_manifest": {
                        **inherited["snapshot_manifest"],
                        "hosting_provider": "cloudflare"
                        if inherited["snapshot_manifest"].get("hosting_provider") == "cloudflare"
                        else "external",
                    }
                }
                if inherited.get("snapshot_manifest")
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
            "native_options": native_options,
            "execution_policy": inherited.get("execution_policy")
            or exclusive_policy(
                method, "Retain the parent's serial execution for bounded failure repairs"
            ),
            "fixture_suffix": inherited["fixture_suffix"],
            "minimum_call_interval_seconds": inherited.get(
                "minimum_call_interval_seconds",
                available_methods()[method]["minimum_call_interval_seconds"],
            ),
            "post_call_cooldown_seconds": inherited.get(
                "post_call_cooldown_seconds",
                available_methods()[method]["post_call_cooldown_seconds"],
            ),
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
    if "extraction_instructions" in participant and participant[
        "extraction_instructions"
    ] != contract.get("extraction_instructions", RECORD_INSTRUCTIONS):
        raise ValueError("Repair extraction instructions differ from the parent")
    return contract, rows


def artifact(directory, entry):
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe repair evidence path")
    path = directory / relative
    if digest(path.read_bytes()) != entry["sha256"]:
        raise ValueError("Repair evidence changed")
    return read_json(path)


def fixture_identity(rows):
    return [{k: v for k, v in row.items() if k != "qualification_fetch_attempts"} for row in rows]


def fixture_check(home, base, cases, suffix="", snapshot_manifest=None):
    if snapshot_manifest is not None:
        from harness.web_extract.snapshots import verify_bound_manifest

        return verify_bound_manifest(home, snapshot_manifest, base, case_ids=cases, suffix=suffix)
    from harness.web_extract.snapshots import verify_resources

    return [
        qualify_fixture(home, base, cid, suffix=suffix, timeout=45) for cid in cases
    ] + verify_resources(home, base)


def run(home, profile, method, destination):
    require_archived(home)
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
        "execution/benchmark_execution.py",
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
    require_archived(home)
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
    parent, _ = verify_parent(home, participant)
    if bindings and (
        parent.get("extraction_instruction_delivery") != RECORD_INSTRUCTION_DELIVERY
        or participant.get("extraction_instruction_delivery") != RECORD_INSTRUCTION_DELIVERY
        or "extraction_instructions" not in participant
        or participant["extraction_instructions"]
        != parent.get("extraction_instructions", RECORD_INSTRUCTIONS)
    ):
        raise ValueError(
            "New record repairs require the parent's current instruction delivery and text"
        )
    if (
        plan["timeout_seconds"] != 120
        or plan["calls_per_case"] != 1
        or type(plan.get("access_retries_after_repair", 1)) is not int
        or plan.get("access_retries_after_repair", 1) != 1
    ):
        raise ValueError("Unsupported repair execution budget")
    if plan["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Repair dataset changed")
    code_files = {name: Path(__file__).with_name(name) for name in ("repair.py", "pipeline.py")}
    code_files["benchmark_execution.py"] = Path(__file__).parents[1] / "benchmark_execution.py"
    code = {name: digest(path.read_bytes()) for name, path in code_files.items()}
    contract = {
        "schema": "failed-extraction-repair-run/2"
        if bindings
        else "failed-extraction-repair-run/1",
        "method": method,
        "profile_sha256": digest(profile.read_bytes()),
        "participant": participant,
        "fixture_base": plan["fixture_base"],
        **(
            {"extraction_instruction_delivery": participant["extraction_instruction_delivery"]}
            if "extraction_instruction_delivery" in participant
            else {}
        ),
        **(
            {"extraction_instructions": participant["extraction_instructions"]}
            if "extraction_instructions" in participant
            else {}
        ),
        "access_retry_limit": plan.get("access_retries_after_repair", 1),
        "access_policy": ACCESS_POLICY,
        "fixture_verification": "served-bytes/1",
        "code_sha256": code,
        "code_snapshots": False,
        **bindings,
    }
    if participant.get("execution_policy") is not None:
        policy = ExecutionPolicy.from_dict(participant["execution_policy"])
        contract.update(
            execution_policy=participant["execution_policy"], concurrency=policy.max_in_flight
        )
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
    snapshot = destination / "execution/benchmark_execution.py"
    if snapshot.exists():
        if snapshot.is_symlink() or digest(snapshot.read_bytes()) != code["benchmark_execution.py"]:
            raise ValueError("Repair execution source changed before dispatch")
    else:
        atomic_create(snapshot, code_files["benchmark_execution.py"].read_bytes())
        descriptor = os.open(snapshot.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    receipt = destination / "fixture-before.json"
    if receipt.exists():
        if fixture_identity(read_json(receipt)) != fixture_identity(before):
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
    if fixture_identity(before) != fixture_identity(after):
        raise ValueError("Repair fixtures changed")
    validate_dataset(home)
    if plan["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Repair dataset changed during collection")
    if digest(profile.read_bytes()) != contract["profile_sha256"]:
        raise ValueError("Repair profile changed during collection")
    verify_parent(home, participant)
    if code != {name: digest(path.read_bytes()) for name, path in code_files.items()}:
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
    require_archived(home)
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
    parent_contract = read_json(source / "contract.json")
    if "extraction_instruction_delivery" in participant and (
        participant["extraction_instruction_delivery"]
        != parent_contract.get("extraction_instruction_delivery")
        or contract.get("extraction_instruction_delivery")
        != participant["extraction_instruction_delivery"]
    ):
        raise ValueError("Repair instruction delivery differs from the parent")
    if "extraction_instructions" in participant and (
        participant["extraction_instructions"]
        != parent_contract.get("extraction_instructions", RECORD_INSTRUCTIONS)
        or contract.get("extraction_instructions") != participant["extraction_instructions"]
    ):
        raise ValueError("Repair extraction instructions differ from the parent")
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
        if name not in ("repair.py", "pipeline.py", "benchmark_execution.py"):
            raise ValueError("Unexpected repair execution source")
        snapshot = directory / "execution" / name
        if (name == "benchmark_execution.py" or contract.get("code_snapshots", True)) and (
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
    if fixture_identity(before) != fixture_identity(after):
        raise ValueError("Repair fixture qualification differs")
    needs_fixture = requires_fixture(participant["method"], participant["native_options"])
    if needs_fixture:
        from harness.web_extract.snapshots import validate_fixture_receipts

        validate_fixture_receipts(
            home,
            before,
            contract["fixture_base"],
            participant["case_ids"],
            suffix=participant.get("fixture_suffix", ""),
            bound=participant.get("snapshot_manifest"),
            allow_legacy_manifest="fixture_verification" not in contract,
        )
        if participant["snapshot_manifest"].get("hosting_provider") == "external":
            expected += [
                {
                    "case_id": cid,
                    "url": participant["snapshot_manifest"]["fixture_base"]
                    + "/"
                    + cid
                    + participant.get("fixture_suffix", ""),
                    "sha256": digest((home / "dataset/html" / (cid + ".html")).read_bytes()),
                }
                for cid in participant["case_ids"]
            ]

        def without_hosting_annotation(receipts):
            # Hosting policy belongs to the frozen participant, not the remote
            # manifest. Retained receipts may predate or include that annotation.
            return [
                {
                    **item,
                    "snapshot_manifest": {
                        key: value
                        for key, value in item["snapshot_manifest"].items()
                        if key != "hosting_provider"
                    },
                }
                if isinstance(item.get("snapshot_manifest"), dict)
                else item
                for item in receipts
            ]

        if without_hosting_annotation(before) != without_hosting_annotation(expected):
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


def execution_configuration(contract, directory=None):
    participant = contract.get("participant", contract)
    method = participant.get("method") or contract["methods"][0]
    options = participant.get("native_options", {})
    base = contract.get("fixture_base")
    suffix = participant.get("fixture_suffix", "" if base else ".html")
    snapshot = participant.get("snapshot_manifest")
    if requires_fixture(method, options):
        if snapshot:
            base = snapshot["fixture_base"]
        elif base is None and directory is not None:
            # Historical repair contracts stored the origin only in verified receipts.
            receipts = read_json(directory / "fixture-before.json")
            bases = {
                r["url"].removesuffix("/" + r["case_id"] + suffix)
                for r in receipts
                if "case_id" in r
            }
            if len(bases) != 1:
                raise ValueError("Repair fixture origin is absent or inconsistent")
            base = bases.pop()
    return {
        "native_options": options,
        "fixture_base": base,
        "fixture_suffix": suffix,
        "snapshot_manifest": snapshot,
        **{
            key: participant[key]
            for key in (
                "execution_policy",
                "minimum_call_interval_seconds",
                "post_call_cooldown_seconds",
            )
            if key in participant
        },
    }


def load_selection(home, selection, *, with_configurations=False):
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
    configurations = None
    if with_configurations:
        configuration = execution_configuration(read_json(source / "contract.json"))
        configurations = {row["case_id"]: configuration for row in rows}
    chain = []
    for revision in selection.get("repair", []):
        participant, replacements, _, directory = verify_repair(home, source, revision)
        if [r["sha256"] for r in participant.get("previous_repairs", [])] != chain:
            raise ValueError("Repair predecessor chain differs from current results")
        rows = overlay_repair(rows, replacements, directory)
        if configurations is not None:
            configuration = execution_configuration(
                read_json(directory / "contract.json"), directory
            )
            configurations.update({cid: configuration for cid in replacements})
        chain.append(revision["sha256"])
    return (source, rows, configurations) if with_configurations else (source, rows)


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
