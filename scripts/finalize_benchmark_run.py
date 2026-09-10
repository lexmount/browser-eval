#!/usr/bin/env python3
"""Finalize every worker reference certificate in an aggregated benchmark run."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.reference_lifecycle import finalize_reference_checks  # noqa: E402


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _attempt_key(batch: dict) -> tuple[str, ...]:
    attempts = batch.get("attempt_ids")
    if not isinstance(attempts, list) or not attempts or len(attempts) != len(set(attempts)):
        raise ValueError("Execution batch has invalid attempt IDs")
    return tuple(sorted(attempts))


def finalize_aggregate(run_dir: Path, observations: Path) -> None:
    """Finalize workers, merge certificates, and atomically replace execution.json."""
    run_dir = run_dir.resolve()
    execution_path = run_dir / "execution.json"
    execution = _read(execution_path)
    original = execution.get("batches")
    if not isinstance(original, list) or not original:
        raise ValueError("Aggregated run has no execution batches")
    original_by_key = {_attempt_key(batch): batch for batch in original}
    if len(original_by_key) != len(original):
        raise ValueError("Aggregated execution has duplicate batch attempt sets")

    manifests = sorted((run_dir / "workers").glob("*/*/manifest.json"))
    if not manifests:
        raise ValueError("Aggregated run has no worker manifests")
    before = [_read(path) for path in manifests]
    if {_attempt_key(batch) for batch in before} != set(original_by_key):
        raise ValueError("Worker manifests differ from aggregated execution")

    for path, manifest in zip(manifests, before, strict=True):
        if "reference_checks" in manifest:
            result = finalize_reference_checks(path.parent, observations)
            if result.get("status") != "verified":
                raise ValueError(f"Reference postcheck failed: {path.parent}")

    updated = [_read(path) for path in manifests]
    updated_by_key = {_attempt_key(batch): batch for batch in updated}
    if set(updated_by_key) != set(original_by_key):
        raise ValueError("Finalized worker manifests changed attempt inventory")

    certificate_root = run_dir / "reference-checks"
    for path in manifests:
        source = path.parent / "reference-checks"
        if not source.is_dir():
            continue
        certificate_root.mkdir(exist_ok=True)
        for item in source.iterdir():
            if not item.is_file():
                raise ValueError(f"Unexpected reference certificate entry: {item}")
            target = certificate_root / item.name
            if target.exists():
                if target.read_bytes() != item.read_bytes():
                    raise ValueError(f"Reference certificate collision: {item.name}")
            else:
                shutil.copy2(item, target)

    rebuilt = {**execution, "batches": [updated_by_key[_attempt_key(batch)] for batch in original]}
    temporary = execution_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(execution_path)
    print(f"Finalized aggregate reference checks: {len(updated)} batches")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args()
    finalize_aggregate(args.run_dir, args.observations)


if __name__ == "__main__":
    main()
