"""Persistent locations for measured evidence that must not live in Git."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path


def data_home() -> Path:
    configured = os.environ.get("BROWSER_EVAL_DATA_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".browser-eval"


def current_evidence_dir() -> Path:
    configured = os.environ.get("WEB_FETCH_EVIDENCE_DIR")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else data_home() / "evidence" / "current"
    )


def research_evidence_dir() -> Path:
    configured = os.environ.get("WEB_FETCH_RESEARCH_EVIDENCE_DIR")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else current_evidence_dir().parent / "research"
    )


def require_evidence(path: Path | None = None) -> Path:
    result = (path or current_evidence_dir()).resolve()
    required = ["dataset.json", "results.jsonl", "execution.json", "artifacts"]
    missing = [name for name in required if not (result / name).exists()]
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


def require_canonical_inputs(
    evidence: Path, canonical: Path, canonical_dataset: Path | None = None
) -> None:
    """Reject a report build when its frozen inputs differ from Git."""
    selected_dataset = canonical_dataset or canonical / "dataset.json"
    expected_dataset = json.loads(selected_dataset.read_text())
    scoped_roots = {case["id"] for case in expected_dataset.get("cases", [])}
    if expected_dataset.get("id"):
        scoped_roots.add(expected_dataset["id"])
    # Some frozen suites group newly captured sources beneath a contract-family
    # directory rather than directly beneath each case ID. Include only roots
    # that the canonical dataset explicitly names.
    def declared_paths(value, prefix):
        if isinstance(value, dict):
            for item in value.values():
                yield from declared_paths(item, prefix)
        elif isinstance(value, list):
            for item in value:
                yield from declared_paths(item, prefix)
        elif isinstance(value, str) and value.startswith(prefix + "/"):
            parts = Path(value).parts
            if len(parts) > 1:
                yield Path(*parts[1:])
    declared_sources = set(declared_paths(expected_dataset, "sources"))
    declared_list_references = set(declared_paths(expected_dataset, "list-references"))
    implicit_list_reference_cases = {
        case["id"] for case in expected_dataset.get("cases", [])
        if not case.get("acceptance_contract")
        and case.get("source_benchmark", {}).get("mode") == "dynamic_list_structure"
        and case.get("source_benchmark", {}).get("verification_scope")
        == "first_three_name_detail_pairs"
    }
    for relative in (Path("dataset.json"), Path("sources"), Path("list-references")):
        expected = (
            canonical_dataset
            if relative == Path("dataset.json") and canonical_dataset
            else canonical / relative
        )
        actual = evidence / relative
        if expected.is_file():
            if not actual.is_file():
                raise ValueError(f"Persistent evidence differs from canonical input: {relative}")
            actual_dataset = json.loads(actual.read_text())
            # Cases and shared scoring configuration are canonical. A measured
            # cohort owns its display identity, provider coverage plan and
            # comparison metadata; forcing those into the general dataset would
            # make one report configuration overwrite another.
            for key in ("schema_version", "tool", "dynamic_scoring", "cases"):
                if actual_dataset.get(key) != expected_dataset.get(key):
                    raise ValueError(
                        f"Persistent evidence differs from canonical input: {relative} ({key})"
                    )
            allowed_run_fields = {"id", "name", "coverage_plan", "comparison"}
            unexpected = set(actual_dataset) - set(expected_dataset) - allowed_run_fields
            if unexpected:
                raise ValueError(
                    f"Persistent evidence has unknown dataset fields: {sorted(unexpected)}"
                )
            continue
        def in_scope(path: Path) -> bool:
            if canonical_dataset is None:
                return True
            candidate = path.relative_to(expected)
            first = candidate.parts[0]
            if relative == Path("sources") and declared_sources:
                return candidate in declared_sources or (
                    candidate.name == "evidence.json"
                    and any(item.parent == candidate.parent for item in declared_sources)
                )
            if relative == Path("list-references"):
                return (candidate in declared_list_references
                        or candidate.stem in implicit_list_reference_cases)
            return first in scoped_roots or (
                relative == Path("list-references") and Path(first).stem in scoped_roots
            )

        expected_files = {
            path.relative_to(expected): path.read_bytes()
            for path in expected.rglob("*")
            if path.is_file() and in_scope(path)
        }
        actual_files = (
            {
                path.relative_to(actual): path.read_bytes()
                for path in actual.rglob("*")
                if path.is_file()
            }
            if actual.is_dir()
            else {}
        )
        if actual_files != expected_files:
            raise ValueError(f"Persistent evidence differs from canonical input tree: {relative}")


def require_current_report_profile(evidence: Path, profile_path: Path) -> None:
    """Reject evidence from another valid cohort when building the current report."""
    profile = json.loads(profile_path.read_text())
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
    if comparison.get("kind") != profile["comparison_kind"]:
        failures.append("comparison_kind")
    if comparison.get("timeout_seconds") != profile["timeout_seconds"]:
        failures.append("timeout_seconds")
    if comparison.get("runs_per_case") != profile["runs_per_case"]:
        failures.append("runs_per_case")
    if comparison.get("conversion") != profile["conversion"]:
        failures.append("conversion")
    for key in (
        "concurrency",
        "reference_binding_mode",
        "publication_state",
        "unverified_reference_cases",
        "require_independent_result_review",
    ):
        if comparison.get(key) != profile[key]:
            failures.append(key)
    tracks = {case.get("track", "web") for case in cases}
    if set(coverage) != tracks or any(
        len(names) != len(providers) or set(names) != providers for names in coverage.values()
    ):
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
        for provider in providers
        for case_id in case_ids
        for _ in range(profile["runs_per_case"])
    )
    actual_matrix = Counter((row.get("provider"), row.get("case_id")) for row in records)
    if actual_matrix != expected_matrix:
        failures.append("provider_case_matrix")
    attempt_ids = [row.get("attempt_id") for row in records]
    if any(not identifier for identifier in attempt_ids) or len(attempt_ids) != len(
        set(attempt_ids)
    ):
        failures.append("attempt_ids")
    if failures:
        raise ValueError(
            "Evidence does not match evaluation/current-report.json: " + ", ".join(failures)
        )
