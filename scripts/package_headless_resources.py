"""Build a bound client-side CPU, memory and network comparison from a reviewed cohort."""

import argparse
from copy import deepcopy
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import percentile, task_passed
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.result_review import require_result_review


METRICS = (
    "measurement_duration_ms",
    "cpu_ms",
    "cpu_average_utilization_pct",
    "cpu_sampled_utilization_p95_pct",
    "memory_mean_bytes",
    "memory_p95_bytes",
    "memory_peak_bytes",
    "memory_mib_seconds",
    "network_rx_bytes",
    "network_tx_bytes",
    "network_rx_bytes_per_second",
    "network_tx_bytes_per_second",
)


def _stats(values):
    return {
        "n": len(values),
        "p50": statistics.median(values),
        "p95": percentile(values, 0.95),
        "mean": statistics.fmean(values),
    }


def value(record, metric):
    return (record.get("provider_metadata") or {}).get("client_resource", {}).get(metric)


def package(root: Path) -> dict:
    dataset = json.loads((root / "dataset.json").read_text())
    if (dataset.get("comparison") or {}).get("kind") != "headless_client_resource_profile":
        raise ValueError("Resource cohort has the wrong comparison kind")
    cases = {case["id"]: case for case in dataset["cases"]}
    records = [json.loads(line) for line in (root / "results.jsonl").read_text().splitlines()]
    batches = (
        json.loads((root / "execution.json").read_text())["batches"]
        if (root / "execution.json").is_file()
        else [json.loads((root / "manifest.json").read_text())]
    )
    require_verified_reference_checks(batches, cases=dataset, records=records, source=root,
                                      allow_scoring_rule_revision=True)
    require_result_review(root, dataset, records)
    providers = sorted({record["provider"] for record in records})
    expected = {(provider, case_id) for provider in providers for case_id in cases}
    observed = {(record["provider"], record["case_id"]) for record in records}
    if len(records) != len(expected) or observed != expected:
        raise ValueError("Resource cohort must contain one complete provider/case matrix")
    missing = [
        f"{record['provider']}/{record['case_id']}/{metric}"
        for record in records
        for metric in METRICS
        if not isinstance(value(record, metric), (int, float))
    ]
    if missing:
        raise ValueError("Resource cohort has missing measurements: " + ", ".join(missing[:8]))
    environments = [
        (record.get("provider_metadata") or {}).get("resource_environment") or {}
        for record in records
    ]
    required_environment = {
        "backend": "docker_cgroup_v2",
        "cgroup_version": "2",
        "network_isolation": "one dedicated bridge with no peer containers per call",
        "controller_excluded": True,
    }
    if any(any(environment.get(key) != expected for key, expected in required_environment.items())
           for environment in environments):
        raise ValueError("Resource cohort is not uniformly Docker/cgroup-v2 isolated")
    invariant_keys = (
        "docker_architecture", "image_id", "cpus", "memory", "pids_limit",
        "sample_interval_ms", "network_isolation", "controller_excluded",
    )
    if any(len({environment.get(key) for environment in environments}) != 1
           for key in invariant_keys):
        raise ValueError("Resource execution environment changed during the cohort")
    common = sorted(
        case_id
        for case_id, case in cases.items()
        if all(
            task_passed(
                next(record for record in records
                     if record["provider"] == provider and record["case_id"] == case_id)["ok"],
                next(record for record in records
                     if record["provider"] == provider and record["case_id"] == case_id)["metrics"],
                case.get("task_kind", "body"),
            )
            for provider in providers
        )
    )
    if len(common) < 5:
        raise ValueError("Too few all-provider successful cases for resource comparison")
    by_key = {(record["provider"], record["case_id"]): record for record in records}
    rows = []
    for provider in providers:
        metrics = {
            metric: _stats([value(by_key[provider, case_id], metric) for case_id in common])
            for metric in METRICS
        }
        rows.append({"provider": provider, "attempts": len(common), "metrics": metrics})
    summary = {
        "schema_version": 1,
        "selection": (
            "Predeclared provider-blind ordinary-web cohort; aggregates use the single "
            "all-provider successful case intersection."
        ),
        "interpretation": "isolated_local_browser_client_container_cost",
        "scope": (
            "Disposable Linux client container cgroup and dedicated network namespace from worker "
            "start through final task-native output; the host controller and container lifecycle "
            "are excluded. Remote methods such as Kitesurf are outside this cohort because their "
            "browser CPU, memory, and upstream network traffic are unavailable."
        ),
        "sampling_limit": (
            "CPU and network totals come from cumulative container counters. Memory and sampled "
            "CPU/rate percentiles use 50 ms Docker/cgroup samples, so shorter-lived peaks can be "
            "missed."
        ),
        "environment": {key: environments[0].get(key) for key in (
            "backend", "docker_server_version", "docker_os", "docker_architecture",
            "cgroup_version", "image_id", "image_repo_digests", "image_architecture",
            "cpus", "memory", "pids_limit", "sample_interval_ms", "network_isolation",
            "controller_excluded",
        )},
        "bindings": {
            "dataset_sha256": hashlib.sha256((root / "dataset.json").read_bytes()).hexdigest(),
            "results_sha256": hashlib.sha256((root / "results.jsonl").read_bytes()).hexdigest(),
        },
        "providers": providers,
        "planned_cases": sorted(cases),
        "common_pass_cases": common,
        "started_at": min(batch["started_at"] for batch in batches),
        "completed_at": max(batch["completed_at"] for batch in batches),
        "rows": rows,
    }
    (root / "resource-comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    if not (root / "execution.json").is_file():
        public_batch = deepcopy(batches[0])
        (public_batch.get("environment") or {}).pop("hostname", None)
        (root / "execution.json").write_text(
            json.dumps({"batches": [public_batch]}, ensure_ascii=False, indent=2) + "\n"
        )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    result = package(parser.parse_args().run)
    print(f"common-pass={len(result['common_pass_cases'])}/{len(result['planned_cases'])} "
          f"providers={len(result['providers'])}")
