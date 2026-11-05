"""Freeze complete archived-page cohorts before calls and certify after collection."""

import argparse
from pathlib import Path

from harness.reporting.templates import metric_contract
from harness.web_extract.dataset import benchmark_config, task_bindings
from harness.web_extract.pipeline import (
    available_methods,
    data_home,
    digest,
    load_run,
    probe,
    read_json,
    require_external_output,
    save_json,
    score_run,
    selected_profile,
    validate_dataset,
    validate_fixture_base,
)


def create_plan(home, methods, output, *, snapshots=None):
    require_external_output(output)
    registry = available_methods()
    if not methods or len(set(methods)) != len(methods) or any(m not in registry for m in methods):
        raise ValueError("Select distinct complete extraction interfaces or the extruct baseline")
    if benchmark_config(home)["adapter"] is None and any(
        registry[method].get("requires_custom_schema") for method in methods
    ):
        raise ValueError("Selected methods require a dataset-provided custom schema")
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
                raise ValueError(
                    "Selected interface cannot consume a public snapshot URL: " + method
                )
            participant["input"] = "url"
            participant["fixture_suffix"] = ""
            if registry[method]["input"] != "url":
                participant["native_options"] = {"input_route": "url"}
        plan["resource_decision"]["memory"] = (
            "Remote memory unavailable; local client memory does not measure the provider."
        )
    save_json(output, plan, exclusive=True)
    return plan


def run(home: Path, profile: Path, method: str, run_dir: Path, fixture_base=None):
    require_external_output(run_dir)
    plan = read_json(profile)
    if plan.get("schema") == "archived-extraction-repair/1":
        from harness.web_extract.repair import run as repair_run

        return repair_run(home, profile, method, run_dir)
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
    if fixture_base is not None:
        validate_fixture_base(fixture_base)
    if available_methods()[method]["input"] == "url" and (
        not fixture_base or not fixture_base.startswith("https://")
    ):
        raise ValueError("URL participant requires frozen HTTPS fixtures before collection")
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
        "fixture_base": fixture_base,
    }
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
    for key in ("native_options", "maximum_physical_calls"):
        if key in participant:
            pacing[key] = participant[key]
    if plan.get("public_snapshots", {}).get("snapshot_manifest"):
        pacing["snapshot_manifest"] = plan["public_snapshots"]["snapshot_manifest"]
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
    ):
        raise ValueError("Certified implementation or plan changed")
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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", type=Path, default=data_home())
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--method", required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--fixture-base")
    a = p.parse_args(argv)
    print(run(a.home, a.profile, a.method, a.run_dir, a.fixture_base))
