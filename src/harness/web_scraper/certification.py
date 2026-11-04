"""Freeze complete archived-page cohorts before calls and certify after collection."""

import argparse
from pathlib import Path

from harness.reporting.templates import metric_contract
from harness.web_scraper.pipeline import (
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
)


def create_plan(home, methods, output):
    require_external_output(output)
    registry = available_methods()
    if not methods or len(set(methods)) != len(methods) or any(m not in registry for m in methods):
        raise ValueError("Select distinct complete extraction interfaces or the extruct baseline")
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
    if selected_profile(home).name == "swde.json":
        from harness.web_scraper.swde import GENERIC_METHODS

        if any(method not in GENERIC_METHODS for method in methods):
            raise ValueError("SWDE requires a complete arbitrary-schema extraction API")
        plan["schema"] = "archived-extraction-full-cohort/1"
        plan["dataset_profile_sha256"] = digest(selected_profile(home).read_bytes())
        plan["task_adapter_sha256"] = digest(Path(__file__).with_name("swde.py").read_bytes())
        for participant in plan["participants"].values():
            participant["maximum_physical_calls"] = len(truth) + 20
    save_json(output, plan, exclusive=True)
    return plan


def run(home: Path, profile: Path, method: str, run_dir: Path, fixture_base=None):
    require_external_output(run_dir)
    plan = read_json(profile)
    if plan.get("schema") == "archived-extraction-repair/1":
        from harness.web_scraper.repair import run as repair_run

        return repair_run(home, profile, method, run_dir)
    if plan.get("task_adapter_sha256"):
        if plan["task_adapter_sha256"] != digest(
            Path(__file__).with_name("swde.py").read_bytes()
        ) or plan["dataset_profile_sha256"] != digest(selected_profile(home).read_bytes()):
            raise ValueError("Frozen task adapter or dataset profile changed")
    participant = plan["participants"][method]
    policy = {"timeout_seconds": 120, "quality_retries": 0, "access_retries_after_repair": 1}
    if any(plan.get(key) != value for key, value in policy.items()):
        raise ValueError("Unsupported frozen timeout or retry policy")
    if available_methods()[method]["input"] == "url" and (
        not fixture_base or not fixture_base.startswith("https://")
    ):
        raise ValueError("URL participant requires frozen HTTPS fixtures before collection")
    binding = run_dir / "certification-precall.json"
    if (
        not binding.exists()
        and run_dir.exists()
        and any(path.name != "execution.lock" for path in run_dir.iterdir())
    ):
        raise ValueError("Pre-call receipt is absent from an already-started execution")
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
    collector = probe
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
    source = collector(
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
    if plan.get("task_adapter_sha256") and (
        plan["task_adapter_sha256"] != digest(Path(__file__).with_name("swde.py").read_bytes())
        or plan["dataset_profile_sha256"] != digest(selected_profile(home).read_bytes())
    ):
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
