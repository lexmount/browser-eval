"""Auditable repairs for browser evaluation run metadata."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def repair_task_scope(artifacts: Path) -> dict[str, object]:
    """Narrow a pre-fix manifest to its fully attempted, non-skipped scope."""
    manifest_path = artifacts / "manifest.json"
    attempts_path = artifacts / "attempts.jsonl"
    backup_path = artifacts / "manifest.superseded-task-scope.json"
    if backup_path.exists():
        raise ValueError(f"superseded manifest already exists: {backup_path}")
    original_bytes = manifest_path.read_bytes()
    manifest = json.loads(original_bytes)
    attempts = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    attempted_ids = {str(row["task_id"]) for row in attempts}
    declared = list(manifest.get("suite") or [])
    declared_ids = {str(row["task_id"]) for row in declared}
    skipped_ids = set((manifest.get("protocol") or {}).get("skipped_qualified_tasks") or [])
    excluded_ids = declared_ids - attempted_ids
    if not attempted_ids or not attempted_ids < declared_ids:
        raise ValueError(
            "repair requires attempts to be a non-empty strict subset of manifest suite"
        )
    if excluded_ids != skipped_ids:
        raise ValueError("unattempted manifest tasks do not exactly match skipped qualified tasks")
    engines = tuple((manifest.get("protocol") or {}).get("engines") or ())
    primary_pairs = {
        (str(row["task_id"]), str(row["engine"]))
        for row in attempts
        if int(row.get("attempt", 0)) == 1
    }
    expected_pairs = {(task_id, engine) for task_id in attempted_ids for engine in engines}
    if primary_pairs != expected_pairs:
        raise ValueError("attempts do not contain exactly one primary task/engine scope")

    backup_path.write_bytes(original_bytes)
    manifest["suite"] = [row for row in declared if str(row["task_id"]) in attempted_ids]
    manifest["protocol"]["task_filter"] = [
        str(row["task_id"]) for row in manifest["suite"]
    ]
    manifest.setdefault("artifact_provenance", {})["manifest_correction"] = {
        "kind": "task_scope_denominator",
        "corrected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "prior_manifest_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "prior_declared_tasks": len(declared_ids),
        "corrected_declared_tasks": len(attempted_ids),
        "excluded_skipped_tasks": len(excluded_ids),
        "superseded_manifest": backup_path.name,
        "measured_attempts_changed": False,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest["artifact_provenance"]["manifest_correction"]
