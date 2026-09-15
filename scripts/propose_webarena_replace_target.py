"""Propose replacing one stale recorded element target while preserving the action path."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.targets import validate_target


def propose_replace_target(
    suite: dict[str, Any],
    task_id: str,
    *,
    action_index: int,
    target: dict[str, Any],
    allow_many: bool = False,
    reason: str,
) -> dict[str, Any]:
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must uniquely contain {task_id}")
    task = matches[0]
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    if action_index < 1 or action_index > len(actions):
        raise ValueError("action index is outside the trajectory")
    action = actions[action_index - 1]
    if action.get("action") not in {"click", "double_click", "press"}:
        raise ValueError("target replacement requires a targetable pointer or key action")
    if not reason.strip():
        raise ValueError("target replacement requires a reason")
    validate_target({"target": target})
    action.pop("selector", None)
    action["target"] = deepcopy(target)
    if allow_many:
        action["allow_many"] = True
    else:
        action.pop("allow_many", None)
    response_builder = (task.get("ground_truth") or {}).get("response_builder")
    if not isinstance(response_builder, dict):
        raise ValueError("target replacement requires an existing response builder")
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": deepcopy(response_builder),
        "reason": reason.strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--action-index", type=int, required=True)
    parser.add_argument("--target-json", required=True)
    parser.add_argument("--allow-many", action="store_true")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = json.loads(args.target_json)
    if not isinstance(target, dict):
        raise ValueError("target JSON must be an object")
    proposal = propose_replace_target(
        json.loads(args.suite.read_text(encoding="utf-8")),
        args.task_id,
        action_index=args.action_index,
        target=target,
        allow_many=args.allow_many,
        reason=args.reason,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
