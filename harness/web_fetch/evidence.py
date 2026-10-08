"""Append and project complete provider cohorts while preserving physical calls."""

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

from harness.record_io import load_jsonl, parse_jsonl

from .artifacts import (
    materialize_bound_artifacts,
    materialize_reference_checks,
    materialize_task_references,
    verify_record_artifacts,
)
from .reference_inputs import input_tree
from .reference_lifecycle import require_verified_reference_checks
from .safety import contained_path, reject_evidence_symlinks, safe_identifier, validate_report_ids


def add_provider_run(canonical: Path, incoming: Path, provider: str, implementation=None, *, track=None):
    for root in (canonical, incoming):
        reject_evidence_symlinks(root)
    canonical, incoming = canonical.resolve(), incoming.resolve()
    safe_identifier(provider)
    dataset = json.loads((canonical / "dataset.json").read_text())
    incoming_dataset = json.loads((incoming / "dataset.json").read_text())
    selected = [c for c in dataset['cases'] if track is None or c.get('track', 'web') == track]
    if not selected:
        raise ValueError('Requested track has no canonical cases')
    if track is None:
        matches = incoming_dataset == dataset
    else:
        matches = (incoming_dataset.get('tool') == dataset.get('tool')
                   and incoming_dataset.get('cases') == selected)
    if not matches:
        raise ValueError("Incoming dataset/reference differs from the canonical dataset")
    # Validate and publish the same byte snapshots, even if incoming files are
    # replaced during staging. Never re-read unvalidated JSONL for publication.
    original = (canonical / "results.jsonl").read_bytes()
    incoming_bytes = (incoming / "results.jsonl").read_bytes()
    if original and not original.endswith(b"\n"):
        raise ValueError("Canonical JSONL must end with newline")
    existing, records = parse_jsonl(original), parse_jsonl(incoming_bytes)
    validate_report_ids(dataset, existing + records)
    cases = {c["id"] for c in selected}
    if any(r["provider"] == provider and r['case_id'] in cases for r in existing):
        raise ValueError("Provider already has canonical records in this scope; refusing replacement")
    if not records or any(r["provider"] != provider for r in records):
        raise ValueError("Incoming records must belong only to the requested provider")
    if Counter(r["case_id"] for r in records) != Counter({c: 1 for c in cases}):
        raise ValueError("Incoming run must cover every case exactly once")
    identifiers = [r["attempt_id"] for r in existing + records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate attempt IDs")
    paths = [contained_path(canonical, r["artifact_path"]) for r in existing + records]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate artifact paths")
    for root, group in [(canonical, existing), (incoming, records)]:
        for r in group:
            verify_record_artifacts(root, r)
            if Path(r['artifact_path']).parts[0] != 'artifacts':
                raise ValueError('Output paths must be inside artifacts/')
        require_verified_reference_checks(
            json.loads((root / 'execution.json').read_text())['batches'],
            cases=dataset if root == canonical else incoming_dataset,
            records=group, source=root)
    if implementation and implementation.get("sha256"):
        if any(
            r["provider_metadata"].get("binary_sha256") != implementation["sha256"] for r in records
        ):
            raise ValueError("Executable fingerprint mismatch")
    batches = deepcopy(json.loads((incoming / "execution.json").read_text())["batches"])
    assigned = []
    for batch in batches:
        if not batch.get("completed_at"):
            raise ValueError("Incoming batch is unfinished")
        names = {p["name"] for p in batch["providers"]}
        selected = [
            r
            for r in records
            if r["provider"] in names
            and batch["started_at"] <= r["started_at"] <= batch["completed_at"]
        ]
        if len(selected) != batch["attempt_count"]:
            raise ValueError("Batch attempt count does not match measured records")
        batch["attempt_ids"] = [r["attempt_id"] for r in selected]
        assigned.extend(batch["attempt_ids"])
        if implementation:
            batch["implementation"] = deepcopy(implementation)
    if Counter(assigned) != Counter(r["attempt_id"] for r in records):
        raise ValueError("Each new attempt must belong to exactly one execution batch")
    execution = json.loads((canonical / "execution.json").read_text())
    execution["batches"].extend(batches)
    plan = dataset.get('coverage_plan')
    if track is not None and (not isinstance(plan, dict) or track not in plan):
        raise ValueError('Track supplement requires an explicit canonical coverage plan')
    if isinstance(plan, dict):
        tracks = {case.get('track', 'web') for case in dataset['cases'] if case['id'] in cases}
        if not tracks <= set(plan):
            raise ValueError('Provider supplement requires an explicit task-group coverage plan')
        for name in tracks:
            plan[name] = sorted(set(plan[name]) | {provider})
    staging = Path(tempfile.mkdtemp(prefix=".evidence-add-", dir=canonical.parent))
    swap = None
    try:
        shutil.copytree(canonical, staging, dirs_exist_ok=True)
        materialize_bound_artifacts(staging, incoming, records)
        materialize_reference_checks(staging, incoming, batches)
        # Existing lines are copied byte-for-byte, not reserialized or rescored.
        (staging / "results.jsonl").write_bytes(
            original + incoming_bytes
        )
        (staging / "execution.json").write_text(
            json.dumps(execution, ensure_ascii=False, indent=2) + "\n"
        )
        if isinstance(plan, dict):
            (staging / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + '\n')
        for row in existing + records:
            verify_record_artifacts(staging, row)
        require_verified_reference_checks(execution['batches'], cases=dataset,
                                          records=existing + records, source=staging)
        swap = Path(tempfile.mkdtemp(prefix=".evidence-swap-", dir=canonical.parent))
        canonical.rename(swap / "original")
        try:
            staging.rename(canonical)
        except BaseException:
            (swap / "original").rename(canonical)
            raise
        shutil.rmtree(swap)
        return len(records)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if swap and swap.exists() and not (swap / "original").exists():
            shutil.rmtree(swap)


def _records(path: Path) -> list[dict]:
    return load_jsonl(path / "results.jsonl")


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


def _project_provider_run(source: Path, output: Path, provider: str, reference_root: Path | None = None,
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
    materialize_task_references(output, {name: input_tree(reference_root/'dataset.json',name)
        for name in ('sources','list-references')}, cases)
    materialize_reference_checks(output, source, projected_batches)
    materialize_bound_artifacts(output, source, selected)
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




def project_provider_run(source: Path, output: Path, provider: str,
                         reference_root: Path | None = None,
                         case_ids: set[str] | None = None) -> None:
    """Expose a new projection only after its complete evidence passes validation."""
    for root in (source, reference_root or source):
        reject_evidence_symlinks(root)
    safe_identifier(provider)
    if output.exists() or output.is_symlink():
        raise ValueError("Projection destination exists: " + str(output))
    source, output = source.resolve(), output.resolve()
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Projection output must be separate from its source")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.' + output.name + '-', dir=output.parent) as work:
        staging = Path(work) / 'evidence'
        _project_provider_run(source, staging, provider, reference_root, case_ids)
        staging.replace(output)


def projection_main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--case-ids", help="Optional comma-separated explicit subset")
    parser.add_argument("--reference-root", type=Path,
                        help="Root containing frozen sources/list-references when the live run omits them")
    args = parser.parse_args(argv)
    case_ids = None
    if args.case_ids is not None:
        values = [value.strip() for value in args.case_ids.split(',')]
        if any(not value for value in values) or len(set(values)) != len(values):
            parser.error('Case IDs must be nonempty and unique')
        case_ids = set(values)
    project_provider_run(args.source, args.output, args.provider, args.reference_root, case_ids)
    print(f"Projected {args.provider}: {args.output}")


def addition_main(argv=None) -> None:
    from .storage import current_evidence_dir

    parser = argparse.ArgumentParser(description='Validate and add a complete provider run.')
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--provider', required=True)
    parser.add_argument('--track', help='Add a complete, previously unmeasured task group')
    parser.add_argument('--implementation', type=Path,
                        help='Verified implementation version/source/hash JSON')
    args = parser.parse_args(argv)
    implementation = json.loads(args.implementation.read_text()) if args.implementation else None
    count = add_provider_run(current_evidence_dir(), args.run_dir, args.provider,
                             implementation, track=args.track)
    print('Added', count, 'records')
