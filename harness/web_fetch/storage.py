"""Persistent locations for measured evidence that must not live in Git."""
from __future__ import annotations

import json
import os
import hashlib
from collections import Counter
from pathlib import Path


def data_home() -> Path:
    configured = os.environ.get("BROWSER_EVAL_DATA_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home()/".browser-eval"


def current_evidence_dir() -> Path:
    configured = os.environ.get("WEB_FETCH_EVIDENCE_DIR")
    return Path(configured).expanduser().resolve() if configured else data_home()/"evidence"/"current"


def research_evidence_dir() -> Path:
    configured = os.environ.get("WEB_FETCH_RESEARCH_EVIDENCE_DIR")
    return (Path(configured).expanduser().resolve() if configured
            else current_evidence_dir().parent/"research")


def require_evidence(path: Path | None = None) -> Path:
    result = (path or current_evidence_dir()).resolve()
    required = ["dataset.json", "results.jsonl", "execution.json", "artifacts"]
    missing = [name for name in required if not (result/name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Persistent benchmark evidence is incomplete at {result}: missing {', '.join(missing)}. "
            "Set WEB_FETCH_EVIDENCE_DIR to an explicit evidence bundle if needed."
        )
    require_selection_history(result)
    return result


def require_selection_history(evidence: Path) -> None:
    """A rerun selection is invalid without the baseline it compares against."""
    if not (evidence / "file-rerun-selection.json").is_file():
        return
    baseline = evidence / "history/file-baseline"
    required = [baseline / "results.jsonl", baseline / "execution.json", baseline / "artifacts"]
    missing = [path.relative_to(evidence).as_posix() for path in required if not path.exists()]
    if missing:
        raise ValueError(
            "File rerun selection requires complete historical baseline evidence: "
            + ", ".join(missing)
        )


def require_canonical_inputs(evidence: Path, canonical: Path) -> None:
    """Reject a report build when its frozen inputs differ from Git."""
    for relative in (Path("dataset.json"), Path("sources"), Path("list-references")):
        expected = canonical / relative
        actual = evidence / relative
        if expected.is_file():
            if not actual.is_file():
                raise ValueError(f"Persistent evidence differs from canonical input: {relative}")
            expected_dataset = json.loads(expected.read_text())
            actual_dataset = json.loads(actual.read_text())
            # Cases and shared scoring configuration are canonical. A measured
            # cohort owns its display identity, provider coverage plan and
            # comparison metadata; forcing those into the general dataset would
            # make one report configuration overwrite another.
            for key in ("schema_version", "tool", "dynamic_scoring", "cases"):
                if actual_dataset.get(key) != expected_dataset.get(key):
                    raise ValueError(f"Persistent evidence differs from canonical input: {relative} ({key})")
            allowed_run_fields = {"id", "name", "coverage_plan", "comparison"}
            unexpected = set(actual_dataset) - set(expected_dataset) - allowed_run_fields
            if unexpected:
                raise ValueError(f"Persistent evidence has unknown dataset fields: {sorted(unexpected)}")
            continue
        expected_files = {
            path.relative_to(expected): path.read_bytes()
            for path in expected.rglob("*")
            if path.is_file()
        }
        actual_files = {
            path.relative_to(actual): path.read_bytes()
            for path in actual.rglob("*")
            if path.is_file()
        } if actual.is_dir() else {}
        if actual_files != expected_files:
            raise ValueError(f"Persistent evidence differs from canonical input tree: {relative}")


def require_current_report_profile(evidence: Path, profile_path: Path) -> None:
    """Reject evidence from another valid cohort when building the current report."""
    profile = json.loads(profile_path.read_text())
    if profile.get("comparison_kind") == "wcxb_frozen_html_to_markdown":
        raise ValueError("WCXB evidence uses the certified cohort validator, not the legacy current-report profile validator")
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in (evidence / "results.jsonl").read_text().splitlines()]
    comparison = dataset.get("comparison", {})
    providers = set(profile["providers"])
    if len(providers) != len(profile["providers"]):
        raise ValueError("evaluation/current-report.json contains duplicate providers")
    coverage = dataset.get("coverage_plan", {})
    cases = dataset.get("cases", [])
    case_ids = [case.get("id") for case in cases]
    failures = []
    if dataset.get("id") != profile["dataset_id"]:
        failures.append("dataset_id")
    if len(dataset.get("cases", [])) != profile["case_count"]:
        failures.append("case_count")
    case_ids_sha256 = hashlib.sha256(json.dumps(
        case_ids, separators=(",", ":"), ensure_ascii=True
    ).encode()).hexdigest()
    if profile.get("case_ids_sha256") != case_ids_sha256:
        failures.append("case_ids_sha256")
    if comparison.get("kind") != profile["comparison_kind"]:
        failures.append("comparison_kind")
    if comparison.get("timeout_seconds") != profile["timeout_seconds"]:
        failures.append("timeout_seconds")
    if comparison.get("runs_per_case") != profile["runs_per_case"]:
        failures.append("runs_per_case")
    if comparison.get("conversion") != profile["conversion"]:
        failures.append("conversion")
    if profile.get("comparison_kind") == "webmainbench_frozen_html_to_markdown":
        latency_diagnostic = profile.get("latency_diagnostic")
        if latency_diagnostic:
            diagnostic_manifest = evidence / "latency-diagnostic/manifest.json"
            if not diagnostic_manifest.is_file() or diagnostic_manifest.is_symlink():
                failures.append("latency_diagnostic")
            else:
                manifest_data = diagnostic_manifest.read_bytes()
                if (hashlib.sha256(manifest_data).hexdigest() != latency_diagnostic.get("manifest_sha256")
                        or json.loads(manifest_data).get("kind") != latency_diagnostic.get("kind")):
                    failures.append("latency_diagnostic")
        revision = profile.get("scoring_revision")
        if revision:
            receipt_path = evidence / "teds-correction.json"
            if (not receipt_path.is_file() or receipt_path.is_symlink()
                    or hashlib.sha256(receipt_path.read_bytes()).hexdigest() != revision.get("receipt_sha256")
                    or json.loads(receipt_path.read_text()).get("kind") != revision.get("kind")):
                failures.append("scoring_revision")
        for key in ("max_failure_retries", "source_dataset_sha256", "official_scorer_commit",
                    "sample_manifest_sha256", "official_metrics", "metric_config", "input_contract"):
            if comparison.get(key) != profile.get(key):
                failures.append(key)
        sample_path = evidence / "sample-manifest.json"
        if (not sample_path.is_file()
                or hashlib.sha256(sample_path.read_bytes()).hexdigest() != profile.get("sample_manifest_sha256")
                or dataset.get("sample_manifest_sha256") != profile.get("sample_manifest_sha256")):
            failures.append("sample_manifest_file")
    for key in ("concurrency", "reference_binding_mode", "publication_state",
                "unverified_reference_cases", "require_independent_result_review"):
        if comparison.get(key) != profile[key]:
            failures.append(key)
    tracks = {case.get("track", "web") for case in cases}
    if (set(coverage) != tracks
            or any(len(names) != len(providers) or set(names) != providers
                   for names in coverage.values())):
        failures.append("provider_coverage")
    if {row.get("provider") for row in records} != providers:
        failures.append("record_providers")
    expected_records = profile["case_count"] * len(providers) * profile["runs_per_case"]
    if len(records) != expected_records:
        failures.append("record_count")
    if len(case_ids) != len(set(case_ids)) or any(not identifier for identifier in case_ids):
        failures.append("case_ids")
    expected_matrix = Counter(
        (provider, case_id)
        for provider in providers for case_id in case_ids
        for _ in range(profile["runs_per_case"])
    )
    actual_matrix = Counter((row.get("provider"), row.get("case_id")) for row in records)
    if actual_matrix != expected_matrix:
        failures.append("provider_case_matrix")
    attempt_ids = [row.get("attempt_id") for row in records]
    if any(not identifier for identifier in attempt_ids) or len(attempt_ids) != len(set(attempt_ids)):
        failures.append("attempt_ids")
    resource_profile = profile.get("resource_profile") or {}
    if resource_profile.get("required"):
        resource_root = evidence / "resource-cohort"
        try:
            resource_dataset = json.loads((resource_root / "dataset.json").read_text())
            resource_records = [json.loads(line) for line in
                                (resource_root / "results.jsonl").read_text().splitlines()]
            resource_summary = json.loads((resource_root / "resource-comparison.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            failures.append("resource_profile_missing")
        else:
            resource_comparison = resource_dataset.get("comparison") or {}
            resource_providers = set(resource_profile["providers"])
            if resource_comparison.get("kind") != resource_profile["comparison_kind"]:
                failures.append("resource_comparison_kind")
            if len(resource_dataset.get("cases", [])) != resource_profile["case_count"]:
                failures.append("resource_case_count")
            if resource_comparison.get("concurrency") != resource_profile["concurrency"]:
                failures.append("resource_concurrency")
            if (resource_comparison.get("resource_sample_interval_ms") !=
                    resource_profile["sample_interval_ms"]):
                failures.append("resource_sample_interval_ms")
            for key, expected in (
                ("resource_backend", resource_profile["backend"]),
                ("resource_cpus", resource_profile["cpus_per_call"]),
                ("resource_memory", resource_profile["memory_per_call"]),
            ):
                if resource_comparison.get(key) != expected:
                    failures.append(key)
            if {row.get("provider") for row in resource_records} != resource_providers:
                failures.append("resource_record_providers")
            expected_resource_records = resource_profile["case_count"] * len(resource_providers)
            if len(resource_records) != expected_resource_records:
                failures.append("resource_record_count")
            required_metrics = set(resource_profile["required_metrics"])
            if any(not required_metrics <= set((row.get("provider_metadata") or {})
                                                .get("client_resource", {}))
                   for row in resource_records):
                failures.append("resource_metrics")
            expected_environment = {
                "backend": resource_profile["backend"],
                "cgroup_version": "2",
                "cpus": resource_profile["cpus_per_call"],
                "memory": resource_profile["memory_per_call"],
                "network_isolation": resource_profile["network_isolation"],
                "controller_excluded": resource_profile["controller_excluded"],
            }
            if any(any(((row.get("provider_metadata") or {})
                        .get("resource_environment") or {}).get(key) != expected
                       for key, expected in expected_environment.items())
                   for row in resource_records):
                failures.append("resource_environment")
            if (set(resource_summary.get("providers", [])) != resource_providers
                    or not resource_summary.get("common_pass_cases")):
                failures.append("resource_summary")
            summary_environment = resource_summary.get("environment") or {}
            if any(summary_environment.get(key) != expected
                   for key, expected in expected_environment.items()):
                failures.append("resource_summary_environment")
    if failures:
        raise ValueError(
            "Evidence does not match evaluation/current-report.json: " + ", ".join(failures)
        )
