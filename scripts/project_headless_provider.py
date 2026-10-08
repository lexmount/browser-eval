#!/usr/bin/env python3
"""Project one complete provider cohort from a retained multi-provider run.

The source may be a physical run with ``manifest.json`` or a certified aggregate
with multiple batches in ``execution.json``. Projection retains the selected
physical calls and their original batch/reference boundaries. No provider call,
score selection, or reference change occurs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.artifacts import materialize_private_artifacts
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.artifacts import artifact_bindings


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text().splitlines()]


def _source_batches(source: Path) -> tuple[list[dict], str, bytes]:
    """Read the authoritative execution boundary without flattening its batches."""
    execution = source / "execution.json"
    if execution.is_file():
        raw = execution.read_bytes()
        payload = json.loads(raw)
        batches = payload.get("batches") if isinstance(payload, dict) else None
        if not isinstance(batches, list) or not batches:
            raise ValueError("Source execution has no batches")
        return batches, "execution", raw
    manifest = source / "manifest.json"
    if manifest.is_file():
        raw = manifest.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Source manifest is invalid")
        return [payload], "manifest", raw
    raise ValueError("Projection source has neither execution.json nor manifest.json")


def _inside_batch(row: dict, batch: dict) -> bool:
    started = datetime.fromisoformat(row["started_at"])
    completed = max(
        datetime.fromisoformat(row.get("completed_at", row["started_at"])),
        started + timedelta(milliseconds=row["elapsed_ms"]),
    )
    lower = datetime.fromisoformat(batch["started_at"])
    upper = datetime.fromisoformat(batch["completed_at"]) + timedelta(milliseconds=.01)
    return lower <= started <= completed <= upper


def _project_batches(
    batches: list[dict], selected: list[dict], provider: str, cases: dict[str, dict],
    *, subset: bool, source_kind: str, source_sha256: str, source_result_count: int,
) -> list[dict]:
    """Crop attempt identities and certificates while retaining physical bounds."""
    rows_by_id = {row["attempt_id"]: row for row in selected}
    ownership = {attempt_id: 0 for attempt_id in rows_by_id}
    identities: dict[str, dict] = {}
    projected = []
    for original in batches:
        attempt_ids = original.get("attempt_ids")
        if not isinstance(attempt_ids, list):
            raise ValueError("Source batch has no attempt ID inventory")
        kept = []
        for attempt_id in attempt_ids:
            row = rows_by_id.get(attempt_id)
            if row is not None and _inside_batch(row, original):
                kept.append(attempt_id)
                ownership[attempt_id] += 1
        if not kept:
            continue
        provider_rows = [row for row in original.get("providers", [])
                         if isinstance(row, dict) and row.get("name") == provider]
        if len(provider_rows) != 1:
            raise ValueError(f"{provider}: implementation identity is missing or ambiguous")
        identity = provider_rows[0]
        identities[json.dumps(identity, ensure_ascii=False, sort_keys=True)] = identity
        batch = deepcopy(original)
        batch["providers"] = [deepcopy(identity)]
        batch["attempt_ids"] = kept
        batch["attempt_count"] = len(kept)
        batch["unexecuted"] = []
        retained_cases = {rows_by_id[attempt_id]["case_id"] for attempt_id in kept}
        batch.setdefault("dataset", {})["case_count"] = len(retained_cases)
        checks = batch.get("reference_checks")
        if checks is not None:
            checks = deepcopy(checks)
            checks["cases"] = {
                case_id: binding for case_id, binding in checks.get("cases", {}).items()
                if case_id in retained_cases
            }
            postcheck = checks.get("postcheck")
            if isinstance(postcheck, dict):
                postcheck["cases"] = {
                    case_id: binding for case_id, binding in postcheck.get("cases", {}).items()
                    if case_id in retained_cases
                }
            if checks["cases"]:
                batch["reference_checks"] = checks
            else:
                batch.pop("reference_checks", None)
        batch["projection"] = {
            "kind": ("complete-provider-case-subset-from-retained-run" if subset else
                     "complete-provider-from-retained-incomplete-matrix"),
            "provider": provider,
            f"source_{source_kind}_sha256": source_sha256,
            "source_result_count": source_result_count,
            "selected_result_count": len(selected),
            "selection": (
                "explicit predeclared provider/case repair scope; no score-based selection"
                if subset else "all frozen cases for one provider; no score-based selection"
            ),
        }
        projected.append(batch)
    if any(count != 1 for count in ownership.values()):
        raise ValueError(
            f"{provider}: selected attempts are missing from, or ambiguous across, execution batches"
        )
    if len(identities) != 1:
        raise ValueError(f"{provider}: implementation identity differs across execution batches")
    return projected


def _bound_file(root: Path, relative: str) -> Path:
    lexical = root.resolve()
    for part in Path(relative).parts:
        lexical = lexical / part
        if lexical.is_symlink():
            raise ValueError("Projection input must not be a symlink: " + relative)
    return contained_path(root, relative)


def _copy_case_references(reference_root: Path, output: Path, case_ids: set[str]) -> None:
    for case_id in sorted(case_ids):
        source_dir = _bound_file(reference_root, f"sources/{case_id}")
        if source_dir.is_dir():
            if source_dir.is_symlink() or any(path.is_symlink() for path in source_dir.rglob("*")):
                raise ValueError("Reference source directory must not be a symlink: " + case_id)
            shutil.copytree(source_dir, contained_path(output, f"sources/{case_id}"))
        list_reference = _bound_file(reference_root, f"list-references/{case_id}.json")
        if list_reference.is_file():
            target = contained_path(output, f"list-references/{case_id}.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(list_reference, target)


def project(source: Path, output: Path, provider: str, reference_root: Path | None = None,
            case_ids: set[str] | None = None) -> None:
    source, output = source.resolve(), output.resolve()
    reference_root = (reference_root or source).resolve()
    if output.exists():
        raise ValueError("Projection destination exists: " + str(output))
    dataset = json.loads((source / "dataset.json").read_text())
    if case_ids is not None:
        known = {case["id"] for case in dataset["cases"]}
        if not case_ids or case_ids - known:
            raise ValueError("Projection case IDs are empty or unknown")
        dataset["cases"] = [case for case in dataset["cases"] if case["id"] in case_ids]
    cases = {case["id"]: case for case in dataset["cases"]}
    all_records = _records(source)
    selected = [row for row in all_records
                if row["provider"] == provider and row["case_id"] in cases]
    expected = set(cases)
    if len(selected) != len(expected) or {row["case_id"] for row in selected} != expected:
        raise ValueError(f"{provider}: provider cohort is incomplete")
    if len({row["attempt_id"] for row in selected}) != len(selected):
        raise ValueError(f"{provider}: duplicate attempt IDs")
    validate_report_ids(dataset, selected)

    source_batches, source_kind, source_document_bytes = _source_batches(source)
    source_document_sha256 = hashlib.sha256(source_document_bytes).hexdigest()
    dataset["coverage_plan"] = {
        track: [provider] for track, providers in dataset["coverage_plan"].items()
        if provider in providers
    }
    if not dataset["coverage_plan"]:
        raise ValueError(f"{provider}: absent from the declared coverage plan")
    projected_batches = _project_batches(
        source_batches, selected, provider, cases, subset=case_ids is not None,
        source_kind=source_kind, source_sha256=source_document_sha256,
        source_result_count=len(all_records),
    )

    certified = any(batch.get("reference_checks", {}).get("status") == "verified"
                    for batch in projected_batches)
    if certified:
        require_verified_reference_checks(
            projected_batches, cases=dataset, records=selected, source=source,
            equivalent_capture_bindings=(
                dataset.get("comparison", {}).get("reference_binding_mode") ==
                "equivalent_reference_bytes_with_independent_batch_captures"
            ),
        )
    else:
        for batch in projected_batches:
            checks = batch.get("reference_checks", {})
            if checks and (checks.get("status") != "awaiting_postcheck" or checks.get("postcheck")):
                raise ValueError("Projection source has incomplete or mixed reference certification")

    output.mkdir(parents=True)
    (output / f"source-{source_kind}.json").write_bytes(source_document_bytes)
    _copy_case_references(reference_root, output, set(cases))
    for batch in projected_batches:
        evidence = batch.get("reference_checks", {}).get("postcheck", {}).get("evidence_path")
        if not evidence:
            continue
        origin = _bound_file(source, evidence)
        expected_hash = batch["reference_checks"]["postcheck"]["evidence_sha256"]
        if not origin.is_file() or hashlib.sha256(origin.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Reference observation evidence changed: " + evidence)
        target = contained_path(output, evidence)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Reference observation evidence collision: " + evidence)
        if not target.exists():
            shutil.copy2(origin, target)
    for row in selected:
        for relative, digest in artifact_bindings(row):
            origin = _bound_file(source, relative)
            if not origin.is_file() or hashlib.sha256(origin.read_bytes()).hexdigest() != digest:
                raise ValueError("Artifact changed: " + relative)
            target = contained_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("Artifact collision: " + relative)
            if not target.exists():
                shutil.copy2(origin, target)
        materialize_private_artifacts(output, source, [row])
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    if len(projected_batches) == 1:
        (output / "manifest.json").write_text(
            json.dumps(projected_batches[0], ensure_ascii=False, indent=2) + "\n"
        )
    (output / "execution.json").write_text(
        json.dumps({"batches": projected_batches}, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in selected))
    if certified:
        require_verified_reference_checks(
            projected_batches, cases=dataset, records=selected, source=output,
            equivalent_capture_bindings=(
                dataset.get("comparison", {}).get("reference_binding_mode") ==
                "equivalent_reference_bytes_with_independent_batch_captures"
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--case-ids", help="Optional comma-separated explicit subset")
    parser.add_argument("--reference-root", type=Path,
                        help="Root containing frozen sources/list-references when the live run omits them")
    args = parser.parse_args()
    case_ids = set(args.case_ids.split(",")) if args.case_ids else None
    project(args.source, args.output, args.provider, args.reference_root, case_ids)
    print(f"Projected {args.provider}: {args.output}")


if __name__ == "__main__":
    main()
