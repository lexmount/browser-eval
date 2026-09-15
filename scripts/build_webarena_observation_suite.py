"""Build a non-canonical suite that captures final visible text for response repair."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.public_webarena.suite import load_suite


def build_observation_suite(
    suite: dict[str, Any], task_ids: list[str], *, output_name: str = "repair_page"
) -> dict[str, Any]:
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("task ids must be a non-empty unique list")
    selected = []
    by_id = {str(task.get("id")): task for task in suite.get("tasks") or []}
    missing = [task_id for task_id in task_ids if task_id not in by_id]
    if missing:
        raise ValueError(f"unknown task ids: {missing}")
    for task_id in task_ids:
        task = deepcopy(by_id[task_id])
        task["trajectory"]["actions"].append(
            {
                "action": "snapshot",
                "selector": "body",
                "save_as": output_name,
                "max_chars": 40_000,
            }
        )
        task["trajectory"]["provenance"] = {
            **(task["trajectory"].get("provenance") or {}),
            "kind": "codex_response_observation_probe",
            "non_canonical": True,
        }
        task["ground_truth"]["expected_actions"] = [
            action["action"] for action in task["trajectory"]["actions"]
        ]
        task["ground_truth"]["response_builder"] = {
            "kind": "observed_shape",
            "task_type": "retrieve",
            "shape": [{"$output": output_name}],
        }
        task["ground_truth"]["qualification"] = {"status": "pending"}
        selected.append(task)
    result = deepcopy(suite)
    result["tasks"] = selected
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "response_observation_probe": {
            "non_canonical": True,
            "task_count": len(selected),
            "output_name": output_name,
            "affects_qualification": False,
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--task-ids", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
    result = build_observation_suite(
        json.loads(args.suite.read_text(encoding="utf-8")), task_ids
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
