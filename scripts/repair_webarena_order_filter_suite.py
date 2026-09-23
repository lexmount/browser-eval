"""Open Magento's order status filter without clearing a nonexistent filter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite


TASK_IDS = (678, 679)


def repair_suite(source_path: Path) -> dict:
    suite = json.loads(source_path.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in suite["tasks"]}
    for task_id in TASK_IDS:
        task = tasks[f"webarena-{task_id}"]
        actions = task["trajectory"]["actions"]
        if (
            len(actions) != 5
            or actions[0].get("action") != "navigate"
            or actions[1].get("selector") != "text=Clear all"
            or actions[2].get("selector") != 'button[data-action="grid-filter-expand"]:visible'
            or actions[3].get("selector") != 'select[name="status"]:visible'
            or actions[4].get("selector") != 'button[data-action="grid-filter-apply"]:visible'
        ):
            raise ValueError(f"task {task_id} order filter trajectory changed")
        actions.pop(1)
        expected = task["ground_truth"]["expected_actions"]
        if expected != ["navigate", "click", "click", "select", "click"]:
            raise ValueError(f"task {task_id} action contract changed")
        expected.pop(1)
        task["trajectory"]["provenance"] = {
            "kind": "expert_repair",
            "repair": "open and apply the status filter in a fresh order-grid session",
        }
    return suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = repair_suite(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_suite(args.output)
    print(json.dumps({"repaired_task_ids": TASK_IDS}))


if __name__ == "__main__":
    main()
