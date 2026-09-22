"""Schedule a reviewed repair subset without replaying any earlier pass."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_repair_queue(
    scope_path: Path,
    ledger_path: Path,
    human_suite_path: Path,
    repair_suite_path: Path,
) -> dict:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    suite = load_suite(repair_suite_path)
    ids = scope.get("task_ids")
    rows = ledger.get("rows") or []
    counts = ledger.get("counts") or {}
    repair_ids = [int(task.task_id.removeprefix("webarena-")) for task in suite.tasks]
    if (
        scope.get("schema") != "lex_webarena_verified_full_scope/1"
        or ids != list(range(scope.get("task_count", -1)))
        or ledger.get("schema") != "lex_webarena_verified_full_ledger/1"
        or ledger.get("scope_sha256") != _sha256(scope_path)
        or [row.get("task_id") for row in rows] != ids
        or counts.get("total") != len(ids)
        or counts.get("pass") != sum(row.get("status") == "pass" for row in rows)
        or not repair_ids
        or len(repair_ids) != len(set(repair_ids))
        or not set(repair_ids) <= set(ids)
    ):
        raise ValueError("repair queue requires a complete audited official scope")
    passed = {row["task_id"] for row in rows if row.get("status") == "pass"}
    if passed & set(repair_ids):
        raise ValueError("repair suite includes a case that has already passed")
    next_rows = []
    for task_id in ids:
        if task_id in passed:
            status, source = "pass", "earlier_pass"
        elif task_id in repair_ids:
            status, source = "pending_first_sweep", "authored_trajectory"
        else:
            status, source = "first_sweep_failed", "earlier_attempt"
        next_rows.append({"task_id": task_id, "status": status, "source": source})
    return {
        "schema": "lex_webarena_verified_full_queue/1",
        "scope_sha256": _sha256(scope_path),
        "preceding_ledger_sha256": _sha256(ledger_path),
        "human_suite_sha256": _sha256(human_suite_path),
        "authored_suite_sha256": _sha256(repair_suite_path),
        "rows": next_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--human-suite", type=Path, required=True)
    parser.add_argument("--repair-suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_repair_queue(args.scope, args.ledger, args.human_suite, args.repair_suite)
    if args.output.exists():
        raise ValueError("repair queue output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"scheduled": sum(row["status"] == "pending_first_sweep" for row in result["rows"])}
        )
    )


if __name__ == "__main__":
    main()
