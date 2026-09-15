"""Build one current logical replay matrix from formal runs and correction shards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .webarena_plan import load_replay_plan
from .webarena_replay import RECEIPT_SCHEMA, validate_qualification_dataset

EFFECTIVE_SCHEMA = "lex_webarena_trajectory_replay_effective_suite/1"


def _read(path: str | Path) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).expanduser().resolve()
    return resolved, json.loads(resolved.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attempt_map(receipt: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    attempts = {
        (str(row["engine"]), int(row["task_id"])): row
        for row in receipt.get("attempts") or []
    }
    expected = {
        (str(engine), int(task_id))
        for engine in receipt.get("engines") or []
        for task_id in receipt.get("task_ids") or []
    }
    if len(attempts) != len(receipt.get("attempts") or []) or set(attempts) != expected:
        raise ValueError("replay receipt does not contain its complete unique result matrix")
    return attempts


def _validate_receipt(
    receipt: dict[str, Any], *, scope: str, engines: list[str]
) -> dict[tuple[str, int], dict[str, Any]]:
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("execution_scope") != scope
        or receipt.get("status") != "complete"
    ):
        raise ValueError(f"expected a completed {scope} replay receipt")
    if list(receipt.get("engines") or []) != engines:
        raise ValueError("replay receipt browser registry differs from the current profile")
    if not all((receipt.get("method_teardown") or {}).get(engine) for engine in engines):
        raise ValueError("replay receipt does not prove method teardown")
    return _attempt_map(receipt)


def build_effective_results(
    *,
    profile_path: str | Path,
    seed_manifest_path: str | Path,
    qualified_dataset_path: str | Path,
    base_receipt_path: str | Path,
    correction_receipt_paths: list[str | Path],
) -> dict[str, Any]:
    """Overlay declared correction shards onto one complete formal base matrix."""

    plan = load_replay_plan(profile_path, seed_manifest_path)
    qualified_sha256 = validate_qualification_dataset(plan, qualified_dataset_path)
    engines = list(plan["engines"])
    base_path, base = _read(base_receipt_path)
    effective = _validate_receipt(base, scope="formal", engines=engines)
    expected = {(engine, task_id) for engine in engines for task_id in plan["task_ids"]}
    if set(effective) != expected:
        raise ValueError("base formal receipt does not cover the complete current task selection")

    source_receipts = [
        {
            "role": "base",
            "path": str(base_path),
            "sha256": _sha256(base_path),
            "run_id": str(base.get("run_id") or ""),
            "task_ids": list(base.get("task_ids") or []),
        }
    ]
    physical_by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in base.get("physical_executions") or []:
        physical_by_key.setdefault((str(row["engine"]), int(row["task_id"])), []).append(row)
    receipts = [base]

    for raw_path in correction_receipt_paths:
        path, receipt = _read(raw_path)
        correction = _validate_receipt(
            receipt, scope="formal_correction_shard", engines=engines
        )
        if (
            receipt.get("profile_sha256") != plan["profile_sha256"]
            or receipt.get("source_seed_manifest_sha256") != plan["seed_manifest_sha256"]
            or receipt.get("qualification_dataset_sha256") != qualified_sha256
        ):
            raise ValueError("formal correction shard is not bound to the current replay inputs")
        effective.update(correction)
        for key in correction:
            physical_by_key[key] = []
        for row in receipt.get("physical_executions") or []:
            physical_by_key[(str(row["engine"]), int(row["task_id"]))].append(row)
        source_receipts.append(
            {
                "role": "correction",
                "path": str(path),
                "sha256": _sha256(path),
                "run_id": str(receipt.get("run_id") or ""),
                "task_ids": list(receipt.get("task_ids") or []),
            }
        )
        receipts.append(receipt)

    if set(effective) != expected or set(physical_by_key) != expected:
        raise ValueError("effective replay matrix is incomplete")
    candidates = {
        int(task["task_id"]): str(task["canonical_candidate_id"])
        for task in plan["seed_manifest"]["tasks"]
    }
    stale = [
        key
        for key, row in effective.items()
        if str(row.get("source_candidate_id") or "") != candidates[key[1]]
    ]
    if stale:
        raise ValueError(f"effective replay matrix contains stale trajectories: {stale}")

    bindings = json.dumps(source_receipts, sort_keys=True, separators=(",", ":"))
    return {
        "schema": EFFECTIVE_SCHEMA,
        "run_id": f"effective-{hashlib.sha256(bindings.encode()).hexdigest()[:12]}",
        "status": "complete",
        "execution_scope": "formal_effective",
        "profile": plan["profile_path"],
        "profile_sha256": plan["profile_sha256"],
        "source_seed_manifest": plan["seed_manifest_path"],
        "source_seed_manifest_sha256": plan["seed_manifest_sha256"],
        "qualification_dataset_sha256": qualified_sha256,
        "task_ids": list(plan["task_ids"]),
        "engines": engines,
        "isolation": dict(plan["profile"]["execution"]),
        "method_teardown": {engine: True for engine in engines},
        "source_receipts": source_receipts,
        "runtime_receipts": [receipt.get("runtime") or {} for receipt in receipts],
        "attempts": [effective[key] for key in sorted(effective)],
        "physical_executions": [
            row for key in sorted(physical_by_key) for row in physical_by_key[key]
        ],
        "counts": {
            "logical_results": len(effective),
            "physical_executions": sum(len(rows) for rows in physical_by_key.values()),
            "passes": sum(row.get("status") == "pass" for row in effective.values()),
            "failures": sum(row.get("status") != "pass" for row in effective.values()),
        },
    }

