"""Propose capturing a visible target's text before a recorded click."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def propose_capture_target(
    suite: dict[str, Any], task_id: str, *, action_index: int, output_name: str
) -> dict[str, Any]:
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must uniquely contain {task_id}")
    task = matches[0]
    if "retrieve" not in (task.get("tags") or []):
        raise ValueError("capture-target repair requires a retrieve task")
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    if action_index < 1 or action_index > len(actions):
        raise ValueError("action index is outside the trajectory")
    clicked = actions[action_index - 1]
    if clicked.get("action") != "click" or not isinstance(clicked.get("target"), dict):
        raise ValueError("selected action must click a structured target")
    if not output_name.strip():
        raise ValueError("output name must be non-empty")
    capture = {
        "action": "get_text",
        "target": deepcopy(clicked["target"]),
        "save_as": output_name,
        "strip": True,
    }
    if "tab_index" in clicked:
        capture["tab_index"] = clicked["tab_index"]
    actions.insert(action_index - 1, capture)
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": {
            "kind": "observed_shape",
            "task_type": "retrieve",
            "shape": [{"$output": output_name}],
        },
        "reason": (
            f"Capture the visible text of recorded click target at step {action_index} before "
            "navigation; the official trajectory already identifies the answer-bearing element."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--action-index", type=int, required=True)
    parser.add_argument("--output-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proposal = propose_capture_target(
        json.loads(args.suite.read_text(encoding="utf-8")),
        args.task_id,
        action_index=args.action_index,
        output_name=args.output_name,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
