#!/usr/bin/env python3
"""Project one complete provider cohort from a retained multi-provider run.

The source run may be globally incomplete after a quarantined, non-cancellable
provider timeout. Projection is allowed only when the selected provider itself
has exactly one logical result for every frozen case. No provider call, score
selection, or reference change occurs.
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

from harness.web_fetch.safety import contained_path, validate_report_ids


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text().splitlines()]


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
    selected = [row for row in _records(source)
                if row["provider"] == provider and row["case_id"] in cases]
    expected = set(cases)
    if len(selected) != len(expected) or {row["case_id"] for row in selected} != expected:
        raise ValueError(f"{provider}: provider cohort is incomplete")
    if len({row["attempt_id"] for row in selected}) != len(selected):
        raise ValueError(f"{provider}: duplicate attempt IDs")
    validate_report_ids(dataset, selected)

    source_manifest_bytes = (source / "manifest.json").read_bytes()
    manifest = json.loads(source_manifest_bytes)
    info = [row for row in manifest.get("providers", []) if row.get("name") == provider]
    if len(info) != 1:
        raise ValueError(f"{provider}: implementation identity is missing or ambiguous")
    checks = deepcopy(manifest.get("reference_checks", {}))
    if checks.get("status") != "awaiting_postcheck" or checks.get("postcheck"):
        raise ValueError("Projection requires the retained unfinished preflight bindings")
    expected_bindings = {case_id for case_id, case in cases.items()
                         if case.get("source_verification", {}).get("reference_sha256")
                         and checks.get("cases", {}).get(case_id)}
    required_bindings = set(checks.get("cases", {})) & set(cases)
    if required_bindings != expected_bindings:
        raise ValueError("Projection reference binding inventory differs from the frozen cases")
    checks["cases"] = {case_id: row for case_id, row in checks.get("cases", {}).items()
                       if case_id in cases}

    dataset["coverage_plan"] = {
        track: [provider] for track, providers in dataset["coverage_plan"].items()
        if provider in providers
    }
    if not dataset["coverage_plan"]:
        raise ValueError(f"{provider}: absent from the declared coverage plan")
    started = min(datetime.fromisoformat(row["started_at"]) for row in selected)
    completed = max(datetime.fromisoformat(row.get("completed_at", row["started_at"]))
                    for row in selected)
    elapsed_completed = max(
        datetime.fromisoformat(row["started_at"]) + timedelta(milliseconds=row["elapsed_ms"])
        for row in selected)
    manifest.update(
        started_at=started.isoformat(), completed_at=max(completed, elapsed_completed).isoformat(),
        providers=info, attempt_count=len(selected),
        attempt_ids=[row["attempt_id"] for row in selected], unexecuted=[],
    )
    if checks["cases"]:
        manifest["reference_checks"] = checks
    else:
        manifest.pop("reference_checks", None)
    manifest["dataset"]["case_count"] = len(cases)
    manifest["projection"] = {
        "kind": ("complete-provider-from-retained-incomplete-matrix"
                 if case_ids is None else "complete-provider-case-subset-from-retained-run"),
        "provider": provider,
        "source_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
        "source_result_count": len(_records(source)),
        "selected_result_count": len(selected),
        "selection": ("all frozen cases for one provider; no score-based selection"
                      if case_ids is None else
                      "explicit predeclared provider/case repair scope; no score-based selection"),
    }

    output.mkdir(parents=True)
    (output / "source-manifest.json").write_bytes(source_manifest_bytes)
    for folder in ("sources", "list-references"):
        origin = reference_root / folder
        if origin.exists():
            shutil.copytree(origin, output / folder)
    for row in selected:
        paths = [(row["artifact_path"], row["metrics"]["content_sha256"])]
        meta = row.get("provider_metadata", {})
        for path_key, hash_key in (("rendered_html_path", "rendered_html_sha256"),
                                   ("raw_stdout_path", "raw_stdout_sha256")):
            if meta.get(path_key):
                paths.append((meta[path_key], meta[hash_key]))
        for item in meta.get("file_evaluation", {}).get("files", []):
            if item.get("artifact_path"):
                paths.append((item["artifact_path"], item["sha256"]))
        for relative, digest in paths:
            origin = contained_path(source, relative)
            if hashlib.sha256(origin.read_bytes()).hexdigest() != digest:
                raise ValueError("Artifact changed: " + relative)
            target = contained_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (output / "execution.json").write_text(json.dumps({"batches": [manifest]}, ensure_ascii=False, indent=2) + "\n")
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in selected))


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
