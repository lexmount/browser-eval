"""Audit the ordered 812-case Chromium acceptance sweep and targeted repairs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.audit_webarena_full_runs import _verify_physical_evidence


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_run(directory: Path) -> tuple[dict, list[dict]]:
    binding_path = directory / "inputs.json"
    progress_path = directory / "progress.jsonl"
    if any(path.is_symlink() or not path.is_file() for path in (binding_path, progress_path)):
        raise ValueError(f"run binding or progress is missing: {directory}")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
    order = binding.get("execution_order")
    if (
        binding.get("schema") != "lex_webarena_full_replay_run/1"
        or not isinstance(order, list)
        or [row.get("task_id") for row in rows] != order[: len(rows)]
        or binding.get("run_count_per_task") != 1
    ):
        raise ValueError(f"run order or protocol changed: {directory}")
    return binding, rows


def _verified_attempt(directory: Path, row: dict) -> dict:
    task_id = row["task_id"]
    if row.get("status") == "pass":
        evaluation_path = _verify_physical_evidence(directory, row)
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        if (
            evaluation.get("task_id") != task_id
            or float(evaluation.get("score") or 0) != 1
            or any(item.get("status") != "pass" for item in evaluation.get("replay_assertions") or [])
        ):
            raise ValueError(f"task {task_id} pass disagrees with official evaluation")
    return {
        "task_id": task_id,
        "status": row.get("status"),
        "score": row.get("score"),
        "progress_sha256": _sha256(directory / "progress.jsonl"),
        "evidence_sha256": row.get("evidence_sha256"),
    }


def audit(scope_path: Path, segment_dirs: list[Path], repair_dirs: list[Path]) -> dict:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    ids = scope.get("task_ids")
    if scope.get("schema") != "lex_webarena_verified_full_scope/1" or ids != list(range(812)):
        raise ValueError("the official 812-case scope changed")
    if not segment_dirs:
        raise ValueError("acceptance segments are required")
    states = {task_id: {"task_id": task_id, "attempts": []} for task_id in ids}
    ordered_ids: list[int] = []
    bindings = []
    reset_sha256 = None
    binary_sha256 = None
    for kind, directories in (("segment", segment_dirs), ("repair", repair_dirs)):
        for directory in directories:
            binding, rows = _read_run(directory)
            if reset_sha256 is None:
                reset_sha256 = binding["site_reset_receipt_sha256"]
                binary_sha256 = binding["binary_sha256"]
            if (
                binding["site_reset_receipt_sha256"] != reset_sha256
                or binding["binary_sha256"] != binary_sha256
            ):
                raise ValueError("acceptance runs used different reset or Chromium bindings")
            bindings.append({
                "kind": kind,
                "inputs_sha256": _sha256(directory / "inputs.json"),
                "progress_sha256": _sha256(directory / "progress.jsonl"),
                "catalog_sha256": binding["catalog_sha256"],
                "runner_revision": binding["runner_revision"],
                "task_ids": [row["task_id"] for row in rows],
            })
            for row in rows:
                task_id = row["task_id"]
                if kind == "segment":
                    ordered_ids.append(task_id)
                elif not states[task_id]["attempts"] or states[task_id]["attempts"][-1]["status"] == "pass":
                    raise ValueError(f"task {task_id} repair does not follow a failure")
                if states[task_id]["attempts"] and states[task_id]["attempts"][-1]["status"] == "pass":
                    raise ValueError(f"task {task_id} repeated after passing")
                states[task_id]["attempts"].append(_verified_attempt(directory, row))
    if ordered_ids != ids:
        raise ValueError("acceptance sweep is incomplete or changed official order")
    rows = []
    for task_id in ids:
        attempts = states[task_id]["attempts"]
        rows.append({
            "task_id": task_id,
            "status": attempts[-1]["status"],
            "attempts": attempts,
        })
    return {
        "schema": "lex_webarena_full_acceptance_ledger/1",
        "scope_sha256": _sha256(scope_path),
        "site_reset_receipt_sha256": reset_sha256,
        "binary_sha256": binary_sha256,
        "runs": bindings,
        "counts": {
            "total": len(ids),
            "pass": sum(row["status"] == "pass" for row in rows),
            "unresolved": sum(row["status"] != "pass" for row in rows),
            "physical_attempts": sum(len(row["attempts"]) for row in rows),
        },
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, default=Path("evaluation/trajectory-replay/webarena-verified-full/scope.json"))
    parser.add_argument("--segment", type=Path, action="append", required=True)
    parser.add_argument("--repair", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-all-pass", action="store_true")
    args = parser.parse_args()
    result = audit(args.scope, args.segment, args.repair)
    if args.require_all_pass and result["counts"]["unresolved"]:
        raise ValueError("the Chromium acceptance sweep has unresolved cases")
    if args.output.exists():
        raise ValueError("acceptance ledger output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
