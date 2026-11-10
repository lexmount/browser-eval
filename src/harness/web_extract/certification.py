"""Freeze complete archived-page cohorts before calls and certify after collection."""

import argparse
import math
import time
from pathlib import Path

from harness.benchmark_execution import ExecutionPolicy, assess_execution
from harness.reporting.templates import metric_contract
from harness.web_extract.dataset import benchmark_config, task_bindings
from harness.web_extract.pipeline import (
    RECORD_INSTRUCTIONS,
    available_methods,
    data_home,
    digest,
    instruction_schema,
    load_run,
    probe,
    read_json,
    require_external_output,
    save_json,
    score_run,
    selected_profile,
    validate_dataset,
    validate_fixture_base,
    validate_instruction_schema,
    validate_native_options,
)


def _preflight_configuration(plan, method):
    return {
        "method": method,
        "dataset_manifest_sha256": plan["dataset_manifest_sha256"],
        "case_ids_sha256": plan["case_ids_sha256"],
        "participant": plan["participants"][method],
        "public_snapshots": plan.get("public_snapshots"),
        "timeout_seconds": plan["timeout_seconds"],
    }


def execution_assessment_authorities(plan, method):
    """Resolve the bound assessment and receipt used by readiness verification."""
    if not plan.get("require_execution_assessment"):
        return ()
    binding = plan.get("execution_assessments", {}).get(method, {})
    source = Path(binding.get("path", ""))
    if (
        not source.is_file()
        or source.is_symlink()
        or digest(source.read_bytes()) != binding.get("sha256")
    ):
        raise ValueError(
            "Complete an execution preflight and bind its assessment before collection"
        )
    assessment = read_json(source)
    assess_execution(assessment, evidence_root=source.parent)
    return source, source.parent / assessment["preflight"]["evidence_path"]


def verify_execution_assessment(plan, method, *, execution_sources=None):
    """Verify readiness evidence at both collection and aggregation boundaries."""
    authorities = execution_assessment_authorities(plan, method)
    if not authorities:
        return
    source, receipt_path = authorities
    assessment = read_json(source)
    participant = plan["participants"][method]
    if (
        assessment["planned_calls"] != plan["case_count"]
        or assessment["execution_policy"] != participant.get("execution_policy")
        or assessment.get("configuration") != _preflight_configuration(plan, method)
    ):
        raise ValueError(
            "Execution assessment belongs to a different method, population or configuration"
        )
    receipt = read_json(receipt_path)
    manifest = receipt_path.parent / "manifest.json"
    if not manifest.is_file() or digest(manifest.read_bytes()) != receipt.get("manifest_sha256"):
        raise ValueError("Execution preflight manifest changed")
    contract, rows = load_run(receipt_path.parent)
    if execution_sources is None:
        owner = Path(__file__)
        execution_sources = {
            "pipeline_sha256": digest(owner.with_name("pipeline.py").read_bytes()),
            "execution_scheduler_sha256": digest(
                (owner.parents[1] / "benchmark_execution.py").read_bytes()
            ),
        }
    if any(
        contract.get(key) != execution_sources.get(key)
        for key in ("pipeline_sha256", "execution_scheduler_sha256")
    ):
        raise ValueError("Execution preflight implementation differs from the collection")
    if (
        contract["kind"] != "execution_preflight"
        or contract["methods"] != [method]
        or len(rows) != receipt["completed_calls"]
        or contract["dataset_manifest_sha256"] != plan["dataset_manifest_sha256"]
        or contract.get("execution_policy") != participant.get("execution_policy")
        or contract.get("native_options", {}) != participant.get("native_options", {})
    ):
        raise ValueError("Execution preflight source differs from the planned cohort")


def create_plan(
    home,
    methods,
    output,
    *,
    snapshots=None,
    native_options=None,
    concurrency=1,
    execution_purpose="latency",
    serial_reason="Isolated per-provider latency",
    require_execution_assessment=False,
    execution_assessments=None,
):
    require_external_output(output)
    registry = available_methods()
    if not methods or len(set(methods)) != len(methods) or any(m not in registry for m in methods):
        raise ValueError("Select distinct complete extraction interfaces")
    if benchmark_config(home)["adapter"] is None and any(
        registry[method].get("requires_custom_schema") for method in methods
    ):
        raise ValueError("Selected methods require a dataset-provided custom schema")
    if any(registry[m].get("retired") for m in methods):
        raise ValueError("Retired reference interfaces cannot enter a new cohort")
    native_options = native_options or {}
    if set(native_options) - set(methods):
        raise ValueError("Native options name methods outside this cohort")
    for method in methods:
        validate_native_options(method, native_options.get(method))
    execution = {}
    service_capacities = {}
    if type(concurrency) is not int or not 1 <= concurrency <= 64:
        raise ValueError("Concurrency must be an integer between 1 and 64")
    for method in methods:
        cooldown = registry[method]["post_call_cooldown_seconds"]
        capacity = 1 if cooldown else concurrency
        resource = method
        reason = (
            f"Provider requires a {cooldown}-second post-call cooldown"
            if cooldown
            else serial_reason
        )
        if registry[method].get("deployment") == "self-hosted-open-source":
            options = native_options[method]
            limit = options["runtime_identity"].get("service_worker_capacity", 1)
            if type(limit) is not int or not 1 <= limit <= 64:
                raise ValueError("Frozen extraction service capacity must be between 1 and 64")
            resource = "extraction-service-" + digest(options["endpoint"].rstrip("/").encode())[:16]
            if resource in service_capacities and service_capacities[resource] != limit:
                raise ValueError("Shared extraction service capacity differs between methods")
            service_capacities[resource] = limit
            capacity = min(capacity, limit)
            reason = f"Frozen extraction service admits {limit} workers"
        value = {
            "purpose": execution_purpose,
            "max_in_flight": capacity,
            "serial_reason": reason,
            "resources": {resource: {"capacity": capacity, "reason": reason}},
            "backend": "process",
        }
        ExecutionPolicy.from_dict(value)
        execution[method] = value
    validate_dataset(home)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    plan = {
        "schema": "archived-product-full-cohort/1",
        "case_count": len(truth),
        "case_ids_sha256": digest(("\n".join(sorted(truth)) + "\n").encode()),
        "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
        "scorer_sha256": read_json(selected_profile(home))["dataset"]["files"]["evaluate.py"],
        "matrix_size": len(truth) * len(methods),
        "participants": {
            m: {
                "minimum_call_interval_seconds": registry[m]["minimum_call_interval_seconds"],
                "post_call_cooldown_seconds": registry[m]["post_call_cooldown_seconds"],
                "input": registry[m]["input"],
                "execution_policy": execution[m],
            }
            for m in methods
        },
        "timeout_seconds": 120,
        "quality_retries": 0,
        "access_retries_after_repair": 1,
        "account_policy": (
            "Use existing allocations; pause on explicit denial; never purchase plans"
        ),
        "review": "Machine scoring only; independent review requires an explicit request",
        "primary_language": "zh",
        "english": "Generated only on explicit request",
        "metric_contract": metric_contract(),
        "resource_decision": {
            "cpu": "Remote CPU unavailable; client CPU does not measure the provider.",
            "memory": "Remote memory unavailable; the local baseline is timed separately.",
        },
    }
    if benchmark_config(home)["adapter"] is not None:
        if any(not registry[method].get("custom_schema") for method in methods):
            raise ValueError("This dataset requires a complete arbitrary-schema extraction API")
        plan["schema"] = "archived-extraction-full-cohort/1"
        plan.update(task_bindings(home))
        for participant in plan["participants"].values():
            participant["maximum_physical_calls"] = len(truth) + 20
    if snapshots is None and any(
        registry[m]["input"] == "url" or "input_route" in registry[m]["options"] for m in methods
    ):
        raise ValueError(
            "URL-capable interfaces require --snapshots; upload-only methods may omit it"
        )
    if snapshots is not None:
        from harness.web_extract.snapshots import select

        plan["public_snapshots"] = select(home, read_json(snapshots))
        validate_fixture_base(plan["public_snapshots"]["fixture_base"])
        if (
            not plan["public_snapshots"].get("snapshot_manifest")
            and plan["public_snapshots"].get("hosting_provider") != "external"
        ):
            raise ValueError("Managed cohorts require a versioned dataset snapshot; run host first")
        for method, participant in plan["participants"].items():
            if (
                registry[method]["input"] != "url"
                and "input_route" not in registry[method]["options"]
            ):
                continue
            participant["input"] = "url"
            participant["fixture_suffix"] = ""
            if registry[method]["input"] != "url":
                participant["native_options"] = {"input_route": "url"}
        plan["resource_decision"]["memory"] = (
            "Remote memory unavailable; local client memory does not measure the provider."
        )
    adapter = benchmark_config(home)["adapter"]
    for method, options in native_options.items():
        participant = plan["participants"][method]
        participant["native_options"] = {**participant.get("native_options", {}), **options}
        effective_input = participant["native_options"].get(
            "input_route", registry[method]["input"]
        )
        if participant["input"] != effective_input:
            raise ValueError("Native options conflict with the frozen participant input route")
    if adapter is not None:
        for case in truth.values():
            schema = instruction_schema(adapter.task_schema(home, case), RECORD_INSTRUCTIONS)
            for method in methods:
                validate_instruction_schema(method, schema)
    if require_execution_assessment:
        plan["require_execution_assessment"] = True
    for source in execution_assessments or []:
        assessment = read_json(source)
        assess_execution(assessment, evidence_root=source.parent)
        method = assessment.get("configuration", {}).get("method")
        if method not in methods or method in plan.get("execution_assessments", {}):
            raise ValueError("Execution assessment must select a distinct planned method")
        plan.setdefault("execution_assessments", {})[method] = {
            "path": str(source),
            "sha256": digest(source.read_bytes()),
        }
    save_json(output, plan, exclusive=True)
    return plan


def run(
    home: Path,
    profile: Path,
    method: str,
    run_dir: Path,
    fixture_base=None,
    *,
    sample_case_ids=None,
):
    require_external_output(run_dir)
    plan = read_json(profile)
    if plan.get("schema") == "archived-extraction-repair/1":
        from harness.web_extract.repair import run as repair_run

        return repair_run(home, profile, method, run_dir)
    selected_base = plan.get("public_snapshots", {}).get("fixture_base")
    if fixture_base is not None or selected_base is not None:
        validate_fixture_base(fixture_base if fixture_base is not None else selected_base)
    binding = run_dir / "certification-precall.json"
    if (
        not binding.exists()
        and run_dir.exists()
        and any(path.name != "execution.lock" for path in run_dir.iterdir())
    ):
        raise ValueError("Pre-call receipt is absent from an already-started execution")
    policy = {"timeout_seconds": 120, "quality_retries": 0, "access_retries_after_repair": 1}
    if any(plan.get(key) != value for key, value in policy.items()):
        raise ValueError("Unsupported frozen timeout or retry policy")
    if plan.get("public_snapshots"):
        from harness.web_extract.snapshots import inventory

        selected = plan["public_snapshots"]
        version, _ = inventory(home)
        if version != selected["html_inventory_sha256"]:
            raise ValueError("Public snapshot input version changed")
        if fixture_base is not None and fixture_base != selected["fixture_base"]:
            raise ValueError("Cannot override the frozen public snapshot URL")
        fixture_base = selected["fixture_base"]
    participant = plan["participants"][method]
    if participant.get("input") == "html" and plan.get("public_snapshots"):
        fixture_base = None
    if available_methods()[method]["input"] == "url" and (
        not fixture_base or not fixture_base.startswith("https://")
    ):
        raise ValueError("URL participant requires frozen HTTPS fixtures before collection")
    effective_input = participant.get("native_options", {}).get(
        "input_route", available_methods()[method]["input"]
    )
    if participant.get("input", effective_input) != effective_input:
        raise ValueError("Native options conflict with the frozen participant input route")
    records = benchmark_config(home)["adapter"] is not None
    if records and plan.get("schema") != "archived-extraction-full-cohort/1":
        raise ValueError("Frozen plan schema differs from the selected record benchmark")
    if records or plan.get("task_adapter_sha256"):
        bindings = task_bindings(home)
        if not bindings or any(plan.get(key) != value for key, value in bindings.items()):
            raise ValueError("Frozen task adapter or dataset profile changed")
    validate_dataset(home)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    case_hash = digest(("\n".join(sorted(truth)) + "\n").encode())
    if plan.get("dataset_manifest_sha256") != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Frozen plan reference manifest differs")
    if (
        plan.get("scorer_sha256")
        != read_json(selected_profile(home))["dataset"]["files"]["evaluate.py"]
    ):
        raise ValueError("Frozen plan scorer differs")
    if len(truth) != plan["case_count"] or case_hash != plan["case_ids_sha256"]:
        raise ValueError("Full cohort population differs from the frozen plan")
    if sample_case_ids is None:
        verify_execution_assessment(plan, method)
    if sample_case_ids is not None and (run_dir / "certificate.json").exists():
        raise ValueError("A preflight must not reuse a certified full cohort directory")
    if (run_dir / "certificate.json").exists():
        if read_json(binding).get("fixture_base") != fixture_base:
            raise ValueError("Certified resume inputs changed: fixture_base")
        contract, rows = load_run(run_dir)
        certificate = read_json(run_dir / "certificate.json")
        for field, path in (
            ("profile_sha256", profile),
            ("manifest_sha256", run_dir / "manifest.json"),
            ("scores_sha256", run_dir / "scores.json"),
            ("precall_sha256", binding),
        ):
            if certificate[field] != digest(path.read_bytes()):
                raise ValueError("Completed cohort binding changed: " + field)
        if contract["methods"] != [method] or certificate["method"] != method:
            raise ValueError("Completed cohort method differs")
        return {
            "run": str(run_dir),
            "cases": len(rows),
            "scores": read_json(run_dir / "scores.json")["scores"],
        }
    precall = {
        "schema": "archived-extraction-precall/1",
        "profile_sha256": digest(profile.read_bytes()),
        "method": method,
        "case_ids_sha256": case_hash,
        "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
        "pipeline_sha256": digest(Path(__file__).with_name("pipeline.py").read_bytes()),
        "execution_scheduler_sha256": digest(
            (Path(__file__).parents[1] / "benchmark_execution.py").read_bytes()
        ),
        "fixture_base": fixture_base,
    }
    if sample_case_ids is not None:
        precall["stage"] = "execution_preflight"
        precall["sample_case_ids"] = sample_case_ids
        precall["started_at_unix"] = (
            read_json(binding)["started_at_unix"] if binding.exists() else time.time()
        )
    binding = run_dir / "certification-precall.json"
    if binding.exists():
        original = read_json(binding)
        if (run_dir / "contract.json").exists():
            if original["pipeline_sha256"] != precall["pipeline_sha256"]:
                raise ValueError(
                    "Resume contract changed: pipeline_sha256; use the original commit"
                )
        elif any(
            (run_dir / name).exists() for name in ("calls.journal.jsonl", "inflight.json")
        ) or any((run_dir / "calls").glob("*")):
            raise ValueError("Cannot recover initialization with unbound dispatch evidence")
        if original != precall:
            raise ValueError("Certified resume inputs changed")
    else:
        save_json(binding, precall, exclusive=True)
    pacing = (
        {"post_call_cooldown": participant["post_call_cooldown_seconds"]}
        if participant.get("post_call_cooldown_seconds")
        else {}
    )
    if "fixture_suffix" in participant:
        pacing["fixture_suffix"] = participant["fixture_suffix"]
    for key in ("native_options", "maximum_physical_calls", "execution_policy"):
        if key in participant:
            pacing[key] = participant[key]
    if fixture_base and plan.get("public_snapshots", {}).get("snapshot_manifest"):
        selected = plan["public_snapshots"]
        pacing["snapshot_manifest"] = {
            **selected["snapshot_manifest"],
            **(
                {"hosting_provider": selected["hosting_provider"]}
                if selected.get("hosting_provider") in {"external", "cloudflare"}
                else {}
            ),
        }
    if sample_case_ids is not None:
        pacing["sample_case_ids"] = sample_case_ids
        pacing["maximum_physical_calls"] = len(sample_case_ids)
    source = probe(
        home,
        [method],
        full=True,
        fixture_base=fixture_base,
        min_call_interval=participant.get("minimum_call_interval_seconds", 0),
        run_dir=run_dir,
        **pacing,
    )
    contract, rows = load_run(source)
    validate_dataset(home)
    if records and any(plan.get(key) != value for key, value in task_bindings(home).items()):
        raise ValueError("Task adapter or dataset profile changed during collection")
    if (
        digest(profile.read_bytes()) != precall["profile_sha256"]
        or contract["pipeline_sha256"] != precall["pipeline_sha256"]
        or contract.get("execution_scheduler_sha256") != precall["execution_scheduler_sha256"]
    ):
        raise ValueError("Certified implementation or plan changed")
    if sample_case_ids is None:
        verify_execution_assessment(plan, method)
    if sample_case_ids is not None:
        if set(contract["case_ids"]) != set(sample_case_ids) or len(rows) != len(sample_case_ids):
            raise ValueError("Execution preflight is incomplete")
        return {"run": str(source), "cases": len(rows), "diagnostic": True}
    if set(contract["case_ids"]) != set(truth) or len(rows) != len(truth):
        raise ValueError("Certified cohort is incomplete")
    result = score_run(home, source)
    certificate = {
        "schema": "archived-extraction-certificate/1",
        "method": method,
        "case_count": len(rows),
        "precall_sha256": digest(binding.read_bytes()),
        "profile_sha256": precall["profile_sha256"],
        "manifest_sha256": digest((source / "manifest.json").read_bytes()),
        "scores_sha256": digest((source / "scores.json").read_bytes()),
        "dataset_manifest_sha256": precall["dataset_manifest_sha256"],
        "review_scope": "machine scoring only; no independent result review",
    }
    save_json(source / "certificate.json", certificate)
    return {"run": str(source), "cases": len(rows), "scores": result["scores"]}


def execution_preflight(
    home,
    profile,
    method,
    run_dir,
    assessment_output,
    *,
    sample_size=8,
    wall_time_budget_seconds=7200,
):
    """Measure a fixed diagnostic sample through the same collection owner."""
    if type(sample_size) is not int or not 1 <= sample_size <= 64:
        raise ValueError("Preflight sample_size must be between 1 and 64")
    if (
        type(wall_time_budget_seconds) not in (int, float)
        or not math.isfinite(wall_time_budget_seconds)
        or wall_time_budget_seconds <= 0
    ):
        raise ValueError("Preflight wall time budget must be finite and positive")
    require_external_output(assessment_output)
    if not run_dir.resolve().is_relative_to(assessment_output.parent.resolve()):
        raise ValueError("Preflight evidence must live underneath the assessment directory")
    plan = read_json(profile)
    participant = plan["participants"][method]
    policy = participant.get("execution_policy")
    if policy is None:
        raise ValueError("Generate a plan with an explicit execution policy first")
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    if sample_size < min(policy["max_in_flight"], len(truth)):
        raise ValueError("The preflight sample must exercise the planned in-flight limit")
    if assessment_output.exists():
        prior = read_json(assessment_output)
        if (
            prior.get("configuration") != _preflight_configuration(plan, method)
            or prior.get("execution_policy") != policy
            or prior.get("wall_time_budget_seconds") != wall_time_budget_seconds
        ):
            raise ValueError("Execution assessment already binds a different plan")
    groups = {}
    for case_id in sorted(truth):
        groups.setdefault(truth[case_id].get("domain", "product"), []).append(case_id)
    selected = []
    while len(selected) < min(sample_size, len(truth)):
        for group in groups.values():
            if group and len(selected) < sample_size:
                selected.append(group.pop(0))
    receipt_path = run_dir / "execution-preflight.json"
    result = run(home, profile, method, run_dir, sample_case_ids=selected)
    _, rows = load_run(run_dir)
    if not receipt_path.exists():
        receipt = {
            "schema": "benchmark-execution-preflight/1",
            "completed_calls": len(rows),
            # Retain elapsed time across interruptions, including operator downtime.
            # Counting only the resumed tail would underestimate the full matrix.
            "wall_seconds": time.time()
            - read_json(run_dir / "certification-precall.json")["started_at_unix"],
            "max_in_flight": policy["max_in_flight"],
            "case_ids": selected,
            "profile_sha256": digest(profile.read_bytes()),
            "manifest_sha256": digest((run_dir / "manifest.json").read_bytes()),
        }
        save_json(receipt_path, receipt, exclusive=True)
    receipt = read_json(receipt_path)
    if receipt["profile_sha256"] != digest(profile.read_bytes()) or receipt[
        "manifest_sha256"
    ] != digest((run_dir / "manifest.json").read_bytes()):
        raise ValueError("Bound execution preflight evidence changed")
    assessment = {
        "execution_policy": policy,
        "planned_calls": len(truth),
        "setup_seconds": 0,
        "wall_time_budget_seconds": wall_time_budget_seconds,
        "configuration": _preflight_configuration(plan, method),
        "preflight": {
            **{k: receipt[k] for k in ("completed_calls", "wall_seconds", "max_in_flight")},
            "evidence_path": receipt_path.resolve()
            .relative_to(assessment_output.parent.resolve())
            .as_posix(),
            "evidence_sha256": digest(receipt_path.read_bytes()),
        },
    }
    summary = assess_execution(assessment, evidence_root=assessment_output.parent)
    if assessment_output.exists() and read_json(assessment_output) != assessment:
        raise ValueError("Execution assessment already exists with different contents")
    if not assessment_output.exists():
        save_json(assessment_output, assessment, exclusive=True)
    return {**result, "assessment": str(assessment_output), **summary}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", type=Path, default=data_home())
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--method", required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--fixture-base")
    a = p.parse_args(argv)
    print(run(a.home, a.profile, a.method, a.run_dir, a.fixture_base))
