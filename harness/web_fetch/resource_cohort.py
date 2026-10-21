"""Build a bound client-side CPU, memory and network comparison from a reviewed cohort."""

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

from harness.record_io import load_jsonl
from harness.web_fetch.publication_privacy import project_execution_manifest

from .artifacts import verify_record_artifacts
from lexbench_webfetch.metrics import percentile, task_passed
from .reference_lifecycle import require_verified_reference_checks
from .result_review import require_result_review
from lexbench_webfetch.paths import contained_path
from .speed_cohort import provider_cohort

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
        "p50": statistics.median(values) if values else None,
        "p95": percentile(values, 0.95) if values else None,
        "mean": statistics.fmean(values) if values else None,
    }


def value(record, metric):
    return (record.get("provider_metadata") or {}).get("client_resource", {}).get(metric)


def build_summary(dataset: dict, records: list[dict], batches: list[dict]) -> dict:
    if (dataset.get("comparison") or {}).get("kind") != "headless_client_resource_profile":
        raise ValueError("Resource cohort has the wrong comparison kind")
    cases = {case["id"]: case for case in dataset["cases"]}
    if not cases or len(cases) != len(dataset["cases"]):
        raise ValueError("Resource cohort has empty or duplicate cases")
    providers = sorted(provider_cohort(dataset))
    expected = {(provider, case_id) for provider in providers for case_id in cases}
    observed = {(record["provider"], record["case_id"]) for record in records}
    if len(records) != len(expected) or observed != expected:
        raise ValueError("Resource cohort must contain one complete provider/case matrix")
    missing = [
        f"{record['provider']}/{record['case_id']}/{metric}"
        for record in records
        for metric in METRICS
        if (isinstance(value(record, metric), bool)
                or not isinstance(value(record, metric), (int, float))
                or not math.isfinite(value(record, metric)) or value(record, metric) < 0)
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
    for environment in environments:
        interval = environment.get("sample_interval_ms")
        if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or interval <= 0):
            raise ValueError("Resource cohort requires a recorded positive sample_interval_ms")
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
    by_key = {(record["provider"], record["case_id"]): record for record in records}
    rows = []
    for provider in providers:
        metrics = {
            metric: _stats([value(by_key[provider, case_id], metric) for case_id in cases])
            for metric in METRICS
        }
        successful = [case_id for case_id, case in cases.items() if task_passed(
            by_key[provider, case_id]['ok'], by_key[provider, case_id]['metrics'],
            case.get('task_kind', 'body'))]
        rows.append({"provider": provider, "attempts": len(cases), "metrics": metrics,
            "successful_attempts": len(successful), "successful_case_ids": sorted(successful),
            "successful_metrics": {metric: _stats([value(by_key[provider, case_id], metric)
                for case_id in successful]) for metric in METRICS}})
    summary = {
        "schema_version": 2,
        "primary_population": "all_declared_calls",
        "conditional_population": "individually_successful_calls",
        "common_pass_population": "diagnostic_only",
        "selection": (
            "Every predeclared provider-task call is included in the primary distribution. "
            "Per-provider successful-call distributions are conditional; the common-pass "
            "intersection is diagnostic only and never selects ranking samples."
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
            f"CPU/rate percentiles use {environments[0]['sample_interval_ms']} ms Docker/cgroup samples, so shorter-lived peaks can be "
            "missed."
        ),
        "environment": {key: environments[0].get(key) for key in (
            "backend", "docker_server_version", "docker_os", "docker_architecture",
            "cgroup_version", "image_id", "image_repo_digests", "image_architecture",
            "cpus", "memory", "pids_limit", "sample_interval_ms", "network_isolation",
            "controller_excluded",
        )},
        "providers": providers,
        "planned_cases": sorted(cases),
        "common_pass_cases": common,
        "started_at": min(batch["started_at"] for batch in batches),
        "completed_at": max(batch["completed_at"] for batch in batches),
        "rows": rows,
    }
    return summary


def load_cohort(root: Path) -> tuple[dict, list[dict], list[dict], dict]:
    dataset_path = contained_path(root, 'dataset.json', reject_symlinks=True)
    results_path = contained_path(root, 'results.jsonl', reject_symlinks=True)
    dataset = json.loads(dataset_path.read_text())
    records = load_jsonl(results_path)
    execution = contained_path(root, 'execution.json', reject_symlinks=True)
    batches = (json.loads(execution.read_text())['batches'] if execution.is_file()
               else [json.loads(contained_path(root, 'manifest.json', reject_symlinks=True).read_text())])
    require_verified_reference_checks(batches, cases=dataset, records=records, source=root,
                                      allow_scoring_rule_revision=True)
    require_result_review(root, dataset, records)
    native_cases = {c['id']: c for c in dataset['cases']}
    for record in records:
        verify_record_artifacts(root, record, native_cases=native_cases)
    summary = build_summary(dataset, records, batches)
    summary['bindings'] = {
        'dataset_sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        'results_sha256': hashlib.sha256(results_path.read_bytes()).hexdigest(),
    }
    return dataset, records, batches, summary


def validate_cohort(root: Path) -> tuple[dict, list[dict], list[dict]]:
    dataset, records, batches, expected = load_cohort(root)
    path = contained_path(root, 'resource-comparison.json', reject_symlinks=True)
    if not path.is_file() or json.loads(path.read_text()) != expected:
        raise ValueError('Resource comparison differs from its complete bound cohort')
    return dataset, records, batches


def package(root: Path) -> dict:
    _, _, batches, summary = load_cohort(root)
    public_batches = [project_execution_manifest(batch) for batch in batches]
    target = contained_path(root, 'resource-comparison.json', reject_symlinks=True)
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    if not (root / 'execution.json').is_file():
        (root / 'execution.json').write_text(
            json.dumps({'batches': public_batches}, ensure_ascii=False, indent=2) + '\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    result = package(parser.parse_args(argv).run)
    print(f"common-pass={len(result['common_pass_cases'])}/{len(result['planned_cases'])} "
          f"providers={len(result['providers'])}")
