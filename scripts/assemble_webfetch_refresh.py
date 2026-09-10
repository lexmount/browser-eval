#!/usr/bin/env python3
"""Assemble the declared twelve-method refresh without selecting results by score."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.reference_lifecycle import (  # noqa: E402
    require_verified_reference_checks,
)
from harness.web_fetch.result_review import require_result_review  # noqa: E402
from harness.web_fetch.safety import contained_path, validate_report_ids  # noqa: E402
from scripts.assemble_headless import _prune_shadowed_batch_attempts  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _read_records(root: Path) -> list[dict]:
    path = contained_path(root, "results.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid result rows: {path}")
    return rows


def _provider_order(dataset: dict) -> list[str]:
    coverage = dataset.get("coverage_plan")
    if not isinstance(coverage, dict) or not coverage:
        raise ValueError("Canonical dataset has no provider coverage plan")
    orders = [names for names in coverage.values() if isinstance(names, list)]
    if (
        len(orders) != len(coverage)
        or not orders
        or any(names != orders[0] for names in orders[1:])
    ):
        raise ValueError("Every track must use the same ordered provider cohort")
    providers = orders[0]
    if not providers or len(providers) != len(set(providers)):
        raise ValueError("Canonical provider cohort is empty or duplicated")
    return providers


def expected_refresh_pairs(plan: dict, canonical: dict) -> set[tuple[str, str]]:
    """Return the exact main refresh scope, rejecting an internally inconsistent plan."""
    matrix = plan.get("main_matrix")
    if plan.get("schema_version") != 1 or not isinstance(matrix, dict):
        raise ValueError("Unsupported refresh plan")
    providers = _provider_order(canonical)
    cases = [case.get("id") for case in canonical.get("cases", [])]
    if not cases or len(cases) != len(set(cases)) or any(not case for case in cases):
        raise ValueError("Canonical case inventory is empty or duplicated")
    rerun_cases = matrix.get("rerun_case_ids")
    full_providers = matrix.get("full_rerun_providers")
    deadline_ids = matrix.get("deadline_replacement_attempt_ids")
    for label, values in (
        ("rerun_case_ids", rerun_cases),
        ("full_rerun_providers", full_providers),
        ("deadline_replacement_attempt_ids", deadline_ids),
    ):
        if not isinstance(values, list) or len(values) != len(set(values)):
            raise ValueError(f"Refresh plan has invalid {label}")
    if matrix.get("rerun_all_providers_for_each_case") is not True:
        raise ValueError("Refresh cases must be rerun across every provider")
    if not set(rerun_cases) <= set(cases):
        raise ValueError("Refresh plan names a noncanonical case")
    if not set(full_providers) <= set(providers):
        raise ValueError("Refresh plan names a noncanonical provider")
    case_pairs = {(provider, case_id) for case_id in rerun_cases for provider in providers}
    provider_pairs = {(provider, case_id) for provider in full_providers for case_id in cases}
    deadline_pairs = set()
    for attempt_id in deadline_ids:
        if not isinstance(attempt_id, str) or not attempt_id.endswith("__r001"):
            raise ValueError("Deadline replacement must name an r001 attempt")
        stem = attempt_id[:-6]
        try:
            provider, case_id = stem.split("__", 1)
        except ValueError as error:
            raise ValueError("Invalid deadline replacement attempt ID") from error
        pair = (provider, case_id)
        if provider not in providers or case_id not in cases:
            raise ValueError("Deadline replacement lies outside the canonical matrix")
        deadline_pairs.add(pair)
    if deadline_pairs & (case_pairs | provider_pairs):
        raise ValueError("Deadline replacements must be the only remaining pair replacements")
    expected = case_pairs | provider_pairs | deadline_pairs
    if matrix.get("planned_new_calls") != len(expected):
        raise ValueError("Refresh plan call count does not match its declared scopes")
    speed = plan.get("same_window_speed_cohort", {})
    if plan.get("total_planned_new_calls") != len(expected) + speed.get("planned_new_calls", -1):
        raise ValueError("Refresh plan total call count is inconsistent")
    return expected


def _validate_record_identity(
    rows: list[dict], *, allowed_pairs: set[tuple[str, str]], label: str
) -> None:
    validate_report_ids({"cases": [{"id": case_id} for _, case_id in allowed_pairs]}, rows)
    pairs = [(row.get("provider"), row.get("case_id")) for row in rows]
    if len(pairs) != len(set(pairs)):
        raise ValueError(f"{label} contains duplicate provider/case results")
    if set(pairs) != allowed_pairs:
        missing = len(allowed_pairs - set(pairs))
        extra = len(set(pairs) - allowed_pairs)
        raise ValueError(f"{label} differs from its exact matrix: missing={missing}, extra={extra}")
    attempts = [row.get("attempt_id") for row in rows]
    if len(attempts) != len(set(attempts)):
        raise ValueError(f"{label} contains duplicate attempt IDs")
    for row in rows:
        expected = f"{row['provider']}__{row['case_id']}__r001"
        if row.get("run") != 1 or row.get("attempt_id") != expected:
            raise ValueError(
                f"{label} has a noncanonical attempt identity: {row.get('attempt_id')}"
            )


def _artifact_bindings(row: dict) -> list[tuple[str, str]]:
    metrics = row.get("metrics", {})
    result = [(row.get("artifact_path"), metrics.get("content_sha256"))]
    metadata = row.get("provider_metadata", {})
    for path_key, hash_key in (
        ("rendered_html_path", "rendered_html_sha256"),
        ("raw_stdout_path", "raw_stdout_sha256"),
    ):
        if metadata.get(path_key):
            result.append((metadata[path_key], metadata.get(hash_key)))
    for delivered in metadata.get("file_evaluation", {}).get("files", []):
        if delivered.get("artifact_path"):
            result.append((delivered["artifact_path"], delivered.get("sha256")))
    return result


def _verify_artifacts(root: Path, rows: list[dict]) -> None:
    for row in rows:
        for relative, digest in _artifact_bindings(row):
            if not isinstance(relative, str) or not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f"Incomplete artifact binding: {row.get('attempt_id')}")
            source = contained_path(root, relative)
            if not source.is_file() or _sha256(source) != digest:
                raise ValueError(f"Artifact hash mismatch: {row.get('attempt_id')} / {relative}")


def _source_batches(root: Path) -> tuple[list[dict], Path]:
    execution = root / "execution.json"
    manifest = root / "manifest.json"
    if execution.is_file():
        value = _read_json(execution).get("batches")
        if not isinstance(value, list) or not value:
            raise ValueError(f"Invalid execution batches: {root}")
        return value, execution
    if manifest.is_file():
        return [_read_json(manifest)], manifest
    raise ValueError(f"Evidence has no execution or manifest: {root}")


def _validate_source_batches(
    root: Path, dataset: dict, rows: list[dict], *, references: bool
) -> tuple[list[dict], Path]:
    batches, source_path = _source_batches(root)
    ids = Counter(attempt for batch in batches for attempt in batch.get("attempt_ids", []))
    if ids != Counter(row["attempt_id"] for row in rows):
        raise ValueError(f"Execution batches do not exactly cover results: {root}")
    if references:
        require_verified_reference_checks(
            batches,
            cases=dataset,
            records=rows,
            source=root,
            equivalent_capture_bindings=dataset.get("comparison", {}).get("reference_binding_mode")
            == "equivalent_reference_bytes_with_independent_batch_captures",
        )
    return batches, source_path


def _require_canonical_cases(
    dataset: dict,
    canonical_cases: dict[str, dict],
    rows: list[dict],
    label: str,
    *,
    equivalent_capture_bindings: bool = False,
) -> None:
    supplied = {case.get("id"): case for case in dataset.get("cases", [])}
    if len(supplied) != len(dataset.get("cases", [])):
        raise ValueError(f"{label} has duplicate case definitions")
    for case_id in {row["case_id"] for row in rows}:
        incoming = deepcopy(supplied.get(case_id))
        canonical = deepcopy(canonical_cases.get(case_id))
        if equivalent_capture_bindings and incoming is not None and canonical is not None:
            # Independently refreshed short batches may have different capture
            # timestamps while binding the exact same reference bytes and task
            # contract.  Every other source-verification field remains strict.
            incoming.get("source_verification", {}).pop("checked_at", None)
            canonical.get("source_verification", {}).pop("checked_at", None)
        if incoming != canonical:
            raise ValueError(f"{label} case/reference binding differs from canonical: {case_id}")


def _copy_artifacts(output: Path, selections: list[tuple[dict, Path]]) -> None:
    copied: dict[str, str] = {}
    for row, root in selections:
        for relative, digest in _artifact_bindings(row):
            if relative in copied:
                if copied[relative] != digest:
                    raise ValueError(f"Selected artifacts collide with different bytes: {relative}")
                continue
            source = contained_path(root, relative)
            target = contained_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied[relative] = digest


def _bundle_identity(root: Path, execution_path: Path) -> dict:
    return {
        "label": root.name,
        "dataset_sha256": _sha256(root / "dataset.json"),
        "results_sha256": _sha256(root / "results.jsonl"),
        "execution_file": execution_path.name,
        "execution_sha256": _sha256(execution_path),
    }


def _assemble(
    baseline: Path,
    main_reruns: list[Path],
    speed_cohort: Path,
    canonical_path: Path,
    plan_path: Path,
    output: Path,
) -> None:
    plan = _read_json(plan_path)
    canonical = _read_json(canonical_path)
    canonical_cases = {case["id"]: case for case in canonical.get("cases", [])}
    providers = _provider_order(canonical)
    expected_main = expected_refresh_pairs(plan, canonical)
    expected_final = {(provider, case_id) for provider in providers for case_id in canonical_cases}
    if plan["main_matrix"].get("final_logical_attempts") != len(expected_final):
        raise ValueError("Final logical attempt count differs from the canonical matrix")

    baseline_binding = plan.get("baseline", {})
    if _sha256(baseline / "dataset.json") != baseline_binding.get("dataset_sha256") or _sha256(
        baseline / "results.jsonl"
    ) != baseline_binding.get("results_sha256"):
        raise ValueError("Baseline bytes differ from evaluation/current-rerun.json")
    baseline_dataset = _read_json(baseline / "dataset.json")
    baseline_rows = _read_records(baseline)
    baseline_providers = _provider_order(baseline_dataset)
    baseline_cases = {case["id"]: case for case in baseline_dataset.get("cases", [])}
    baseline_pairs = {
        (provider, case_id) for provider in baseline_providers for case_id in baseline_cases
    }
    if baseline_binding.get("logical_attempts") != len(baseline_rows) or set(
        baseline_providers
    ) != set(providers):
        raise ValueError("Baseline cohort differs from the refresh plan")
    _validate_record_identity(baseline_rows, allowed_pairs=baseline_pairs, label="baseline")
    _verify_artifacts(baseline, baseline_rows)
    baseline_batches, baseline_execution = _validate_source_batches(
        baseline, baseline_dataset, baseline_rows, references=False
    )

    replacements = canonical.get("comparison", {}).get("case_replacements")
    if not isinstance(replacements, dict) or len(set(replacements.values())) != len(replacements):
        raise ValueError("Canonical case replacement map is missing or invalid")
    if set(baseline_cases) - set(canonical_cases) != set(replacements):
        raise ValueError("Baseline-only cases differ from the replacement map")
    if set(canonical_cases) - set(baseline_cases) != set(replacements.values()):
        raise ValueError("Canonical-only cases differ from the replacement map")
    if not set(replacements.values()) <= set(plan["main_matrix"]["rerun_case_ids"]):
        raise ValueError("Every replacement case must be in the all-provider rerun scope")

    rerun_by_pair: dict[tuple[str, str], tuple[dict, Path]] = {}
    rerun_batches: list[tuple[list[dict], list[dict], Path, Path]] = []
    source_identities = []
    equivalent_capture_bindings = (
        canonical.get("comparison", {}).get("reference_binding_mode")
        == "equivalent_reference_bytes_with_independent_batch_captures"
    )
    for root in main_reruns:
        dataset = _read_json(root / "dataset.json")
        rows = _read_records(root)
        _require_canonical_cases(
            dataset,
            canonical_cases,
            rows,
            "main rerun",
            equivalent_capture_bindings=equivalent_capture_bindings,
        )
        validate_report_ids(canonical, rows)
        _verify_artifacts(root, rows)
        batches, execution_path = _validate_source_batches(root, dataset, rows, references=True)
        source_identities.append(_bundle_identity(root, execution_path))
        rerun_batches.append((batches, rows, root, execution_path))
        for row in rows:
            pair = (row.get("provider"), row.get("case_id"))
            if pair in rerun_by_pair:
                raise ValueError(f"Main reruns contain duplicate provider/case results: {pair}")
            rerun_by_pair[pair] = (row, root)
    rerun_rows = [row for row, _ in rerun_by_pair.values()]
    _validate_record_identity(rerun_rows, allowed_pairs=expected_main, label="main reruns")

    baseline_by_pair = {(row["provider"], row["case_id"]): row for row in baseline_rows}
    selected: list[tuple[dict, Path]] = []
    audit = []
    for provider in providers:
        for case in canonical["cases"]:
            pair = (provider, case["id"])
            if pair in rerun_by_pair:
                row, root = rerun_by_pair[pair]
                old_case_id = next(
                    (old for old, new in replacements.items() if new == case["id"]), case["id"]
                )
                old = baseline_by_pair.get((provider, old_case_id))
                selected.append((row, root))
                audit.append(
                    {
                        "attempt_id": row["attempt_id"],
                        "provider": provider,
                        "case_id": case["id"],
                        "replaces_case_id": old_case_id,
                        "selection_reason": "predeclared refresh scope; no score-based selection",
                        "source_results_sha256": _sha256(root / "results.jsonl"),
                        "excluded_attempt": (
                            {
                                key: old.get(key)
                                for key in ("attempt_id", "run_id", "started_at", "elapsed_ms")
                            }
                            if old
                            else None
                        ),
                    }
                )
            else:
                old = baseline_by_pair.get(pair)
                if old is None:
                    raise ValueError(f"No baseline or rerun result for final pair: {pair}")
                selected.append((old, baseline))
    final_rows = [row for row, _ in selected]
    _validate_record_identity(
        final_rows, allowed_pairs=expected_final, label="assembled main matrix"
    )

    speed_plan = plan.get("same_window_speed_cohort", {})
    speed_dataset = _read_json(speed_cohort / "dataset.json")
    speed_rows = _read_records(speed_cohort)
    speed_cases = speed_plan.get("case_ids")
    if (
        speed_plan.get("required") is not True
        or not isinstance(speed_cases, list)
        or len(speed_cases) != len(set(speed_cases))
        or not set(speed_cases) <= set(canonical_cases)
    ):
        raise ValueError("Invalid speed cohort plan")
    if speed_plan.get("provider_count") != len(providers):
        raise ValueError("Speed provider count differs from the main cohort")
    if _provider_order(speed_dataset) != providers:
        raise ValueError("Speed provider cohort differs from the main cohort")
    if {case.get("id") for case in speed_dataset.get("cases", [])} != set(speed_cases):
        raise ValueError("Speed dataset differs from the exact sampled case inventory")
    expected_speed = {(provider, case_id) for provider in providers for case_id in speed_cases}
    if speed_plan.get("planned_new_calls") != len(expected_speed):
        raise ValueError("Speed call count differs from its exact matrix")
    _require_canonical_cases(
        speed_dataset,
        canonical_cases,
        speed_rows,
        "speed cohort",
        equivalent_capture_bindings=equivalent_capture_bindings,
    )
    _validate_record_identity(speed_rows, allowed_pairs=expected_speed, label="speed cohort")
    _verify_artifacts(speed_cohort, speed_rows)
    _speed_batches, speed_execution = _validate_source_batches(
        speed_cohort, speed_dataset, speed_rows, references=True
    )
    if speed_plan.get("require_independent_result_review") is True:
        require_result_review(speed_cohort, speed_dataset, speed_rows)

    output.mkdir(parents=True)
    for folder in ("sources", "list-references"):
        source = canonical_path.parent / folder
        if not source.is_dir():
            raise ValueError(f"Canonical input tree is missing: {folder}")
        shutil.copytree(source, output / folder)
    _copy_artifacts(output, selected)
    for root in [baseline, *main_reruns]:
        if (root / "reference-checks").is_dir():
            shutil.copytree(
                root / "reference-checks", output / "reference-checks", dirs_exist_ok=True
            )

    selected_baseline = [row for row, root in selected if root == baseline]
    batches = _prune_shadowed_batch_attempts(deepcopy(baseline_batches), selected_baseline)
    for source_batches, rows, _root, _ in rerun_batches:
        chosen = [row for row in rows if (row["provider"], row["case_id"]) in expected_main]
        batches.extend(_prune_shadowed_batch_attempts(deepcopy(source_batches), chosen))
    batch_ids = Counter(attempt for batch in batches for attempt in batch.get("attempt_ids", []))
    if batch_ids != Counter(row["attempt_id"] for row in final_rows):
        raise ValueError("Selected execution batches do not exactly cover the final main matrix")

    # Preserve the canonical dataset bytes, not just an equivalent JSON object.
    dataset_bytes = canonical_path.read_bytes()
    results_bytes = b"".join(
        (json.dumps(row, ensure_ascii=True) + "\n").encode() for row in final_rows
    )
    (output / "dataset.json").write_bytes(dataset_bytes)
    (output / "results.jsonl").write_bytes(results_bytes)
    (output / "execution.json").write_text(
        json.dumps({"batches": batches}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "selection-audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    history = output / "history" / "source-executions"
    history.mkdir(parents=True)
    execution_sources = [
        (baseline, baseline_execution),
        *[(root, path) for _, _, root, path in rerun_batches],
    ]
    for index, (root, path) in enumerate(execution_sources):
        shutil.copy2(path, history / f"{index:03d}-{root.name}-{path.name}")

    shutil.copytree(speed_cohort, output / "speed-cohort")
    receipt = {
        "schema_version": 1,
        "kind": "predeclared_webfetch_refresh_assembly",
        "assembled_at": datetime.now(UTC).isoformat(),
        "plan_sha256": _sha256(plan_path),
        "canonical_dataset_sha256": _sha256(canonical_path),
        "baseline": {
            **_bundle_identity(baseline, baseline_execution),
            "selected_attempts": len(selected_baseline),
        },
        "main_rerun_sources": source_identities,
        "declared_new_calls": len(expected_main),
        "selected_rerun_attempts": len(rerun_rows),
        "inherited_attempts": len(selected_baseline),
        "final_logical_attempts": len(final_rows),
        "output_dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "output_results_sha256": hashlib.sha256(results_bytes).hexdigest(),
        "output_execution_sha256": _sha256(output / "execution.json"),
        "selection_audit_sha256": _sha256(output / "selection-audit.json"),
        "speed_cohort": {
            **_bundle_identity(speed_cohort, speed_execution),
            "logical_attempts": len(speed_rows),
            "stored_below": "speed-cohort/",
            "excluded_from_main_results": True,
            "excluded_from_main_execution": True,
        },
        "provider_calls_performed_by_assembler": 0,
        "selection_policy": "exact profile scope; never select by score or retry outcome",
    }
    (output / "refresh-assembly.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def assemble(
    baseline: Path,
    main_reruns: list[Path],
    speed_cohort: Path,
    output: Path,
    *,
    canonical_dataset: Path = ROOT / "evaluation/dataset.json",
    plan: Path = ROOT / "evaluation/current-rerun.json",
) -> None:
    """Validate into a sibling staging directory and expose the result atomically."""
    baseline = baseline.resolve()
    main_reruns = [root.resolve() for root in main_reruns]
    speed_cohort = speed_cohort.resolve()
    output = output.resolve()
    if not main_reruns:
        raise ValueError("At least one main rerun bundle is required")
    if output.exists():
        raise ValueError("Destination exists; evidence must not be overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    staging = work / "evidence"
    try:
        _assemble(
            baseline,
            main_reruns,
            speed_cohort,
            canonical_dataset.resolve(),
            plan.resolve(),
            staging,
        )
        staging.replace(output)
        work.rmdir()
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--main-rerun", required=True, action="append", type=Path)
    parser.add_argument("--speed-cohort", required=True, type=Path)
    parser.add_argument("--canonical-dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--plan", type=Path, default=ROOT / "evaluation/current-rerun.json")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assemble(
        args.baseline,
        args.main_rerun,
        args.speed_cohort,
        args.output,
        canonical_dataset=args.canonical_dataset,
        plan=args.plan,
    )
    print(f"Assembled refresh evidence: {args.output}")


if __name__ == "__main__":
    main()
