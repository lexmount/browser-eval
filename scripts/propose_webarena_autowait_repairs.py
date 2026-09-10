"""Propose minimal no-wait corrections only when trace evidence proves click completion."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def propose_autowait_repair(
    suite: dict[str, Any], qualification: dict[str, Any], task_id: str
) -> dict[str, Any]:
    task_matches = [task for task in suite.get("tasks") or [] if task.get("id") == task_id]
    decision_matches = [
        item for item in qualification.get("tasks") or [] if item.get("task_id") == task_id
    ]
    if len(task_matches) != 1 or len(decision_matches) != 1:
        raise ValueError(f"task and qualification must uniquely contain {task_id}")
    task = task_matches[0]
    decision = decision_matches[0]
    failed = decision.get("failed_step") or {}
    error = str(failed.get("error") or "")
    index = int(failed.get("index") or 0)
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    if (
        decision.get("disposition") != "needs_trace_repair"
        or failed.get("action") != "click"
        or index < 1
        or index > len(actions)
        or "click action done" not in error
        or "waiting for scheduled navigations to finish" not in error
    ):
        raise ValueError(f"{task_id}: evidence does not prove a completed click auto-wait timeout")
    response_builder = (task.get("ground_truth") or {}).get("response_builder")
    if not isinstance(response_builder, dict):
        raise ValueError(f"{task_id}: an existing response builder is required")
    actions[index - 1]["no_wait_after"] = True
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": deepcopy(response_builder),
        "reason": (
            f"Step {index} completed its click but timed out only while Playwright waited for "
            "scheduled navigation; disable that implicit wait and let the next declared locator "
            "provide readiness."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--task-ids", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    qualification = json.loads(args.qualification.read_text(encoding="utf-8"))
    task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("task IDs must be non-empty and unique")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for task_id in task_ids:
        proposal = propose_autowait_repair(suite, qualification, task_id)
        (args.output_dir / f"{task_id}.json").write_text(
            json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
