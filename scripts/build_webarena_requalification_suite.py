"""Reopen the architecture-valid WebArena candidates without discarding prior decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any


def build_requalification_suite(
    suite: dict[str, Any], scope: dict[str, Any], *, predecessor_sha256: str
) -> dict[str, Any]:
    if suite.get("schema") != "lex_browser_use_tasks/4":
        raise ValueError("suite schema is invalid")
    if scope.get("schema") != "lex_browser_use_native_scope/1":
        raise ValueError("native scope schema is invalid")
    included = {str(value) for value in scope.get("included_task_ids") or []}
    excluded = {str(row["task_id"]) for row in scope.get("excluded_tasks") or []}
    task_ids = {str(task["id"]) for task in suite.get("tasks") or []}
    if included & excluded or included | excluded != task_ids:
        raise ValueError("native scope must partition the complete suite")
    result = deepcopy(suite)
    previous = Counter()
    for task in result["tasks"]:
        task_id = str(task["id"])
        qualification = (task.get("ground_truth") or {}).get("qualification") or {}
        previous[str(qualification.get("status") or "missing")] += 1
        if task_id in included:
            task["ground_truth"]["qualification"] = {
                "status": "pending",
                "reason": (
                    "full candidate requalification with complete failure-page evidence; "
                    "prior decisions remain bound by the predecessor suite hash"
                ),
            }
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "requalification": {
            "predecessor_suite_sha256": predecessor_sha256,
            "included_count": len(included),
            "excluded_count": len(excluded),
            "previous_status_counts": dict(sorted(previous.items())),
            "trajectory_policy": "preserve every current canonical action and annotation",
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--native-scope", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite_bytes = args.suite.read_bytes()
    result = build_requalification_suite(
        json.loads(suite_bytes),
        json.loads(args.native_scope.read_text(encoding="utf-8")),
        predecessor_sha256=hashlib.sha256(suite_bytes).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
