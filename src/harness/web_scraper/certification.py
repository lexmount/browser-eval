"""Freeze complete archived-page cohorts before calls and certify after collection."""

import argparse
import importlib.util
import sys
from pathlib import Path

from harness.reporting.templates import metric_contract
from harness.web_scraper.pipeline import (
    PROFILE,
    ROOT,
    available_methods,
    data_home,
    digest,
    load_run,
    probe,
    read_json,
    require_external_output,
    save_json,
    score_run,
    validate_dataset,
)


def create_plan(home, methods, output):
    require_external_output(output)
    registry = available_methods()
    if (
        not methods
        or len(set(methods)) != len(methods)
        or any(m not in registry or registry[m]["input"] == "site_specific" for m in methods)
    ):
        raise ValueError("Select distinct complete extraction interfaces or the extruct baseline")
    validate_dataset(home)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    plan = {
        "schema": "archived-product-full-cohort/1",
        "case_count": len(truth),
        "case_ids_sha256": digest(("\n".join(sorted(truth)) + "\n").encode()),
        "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
        "scorer_sha256": read_json(PROFILE)["dataset"]["files"]["evaluate.py"],
        "matrix_size": len(truth) * len(methods),
        "participants": {
            m: {
                "minimum_call_interval_seconds": 13 if m == "diffbot-product" else 0,
                "post_call_cooldown_seconds": 10 if m == "diffbot-product" else 0,
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
    save_json(output, plan, exclusive=True)
    return plan


def frozen_probe(run_dir, expected_sha):
    source = run_dir / "execution/pipeline.py"
    if source.is_symlink() or digest(source.read_bytes()) != expected_sha:
        raise ValueError("Certified resume execution snapshot changed")
    spec = importlib.util.spec_from_file_location("harness.web_scraper._frozen_runner", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen_profile = module.PROFILE
    module.ROOT, module.PROFILE = ROOT, PROFILE
    # Function defaults are bound at import, before the snapshot is relocated.
    for name in ("prepare", "validate_dataset"):
        function = getattr(module, name, None)
        if function is not None and function.__defaults__ == (frozen_profile,):
            function.__defaults__ = (PROFILE,)
    contract = read_json(run_dir / "contract.json")
    adapter_sha = contract.get("stagehand_sha256")
    adapter = None
    if adapter_sha:
        adapter_source = run_dir / "execution/stagehand.py"
        if adapter_source.is_symlink() or digest(adapter_source.read_bytes()) != adapter_sha:
            raise ValueError("Frozen native adapter changed")
        adapter_spec = importlib.util.spec_from_file_location(
            "harness.web_scraper.stagehand", adapter_source
        )
        adapter = importlib.util.module_from_spec(adapter_spec)
        adapter_spec.loader.exec_module(adapter)

    def collect(*args, **kwargs):
        if adapter is None:
            return module.probe(*args, **kwargs)
        name = "harness.web_scraper.stagehand"
        previous = sys.modules.get(name)
        sys.modules[name] = adapter
        try:
            return module.probe(*args, **kwargs)
        finally:
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    return collect


def run(home: Path, profile: Path, method: str, run_dir: Path, fixture_base=None):
    require_external_output(run_dir)
    plan = read_json(profile)
    participant = plan["participants"][method]
    policy = {"timeout_seconds": 120, "quality_retries": 0, "access_retries_after_repair": 1}
    if any(plan.get(key) != value for key, value in policy.items()):
        raise ValueError("Unsupported frozen timeout or retry policy")
    binding = run_dir / "certification-precall.json"
    if (
        not binding.exists()
        and run_dir.exists()
        and any(path.name != "execution.lock" for path in run_dir.iterdir())
    ):
        raise ValueError("Pre-call receipt is absent from an already-started execution")
    if available_methods()[method]["input"] == "site_specific":
        raise ValueError("A site-specific scraper is not a generic archived-HTML participant")
    validate_dataset(home)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    case_hash = digest(("\n".join(sorted(truth)) + "\n").encode())
    if plan.get("dataset_manifest_sha256") != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Frozen plan reference manifest differs")
    if plan.get("scorer_sha256") != read_json(PROFILE)["dataset"]["files"]["evaluate.py"]:
        raise ValueError("Frozen plan scorer differs")
    if len(truth) != plan["case_count"] or case_hash != plan["case_ids_sha256"]:
        raise ValueError("Full cohort population differs from the frozen plan")
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
            collector = frozen_probe(run_dir, original["pipeline_sha256"])
            precall["pipeline_sha256"] = original["pipeline_sha256"]
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
