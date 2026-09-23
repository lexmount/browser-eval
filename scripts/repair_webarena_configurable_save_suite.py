"""Replace navigation-unsafe Magento save evaluations with a browser click."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite


TASK_IDS = (548, 549, 550)


def repair_suite(source_path: Path) -> dict:
    suite = json.loads(source_path.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in suite["tasks"]}
    for task_id in TASK_IDS:
        task = tasks[f"webarena-{task_id}"]
        actions = task["trajectory"]["actions"]
        old = actions[-1]
        if (
            old.get("action") != "evaluate"
            or "#save-button" not in old.get("script", "")
            or "button.click()" not in old["script"]
        ):
            raise ValueError(f"task {task_id} no longer has the navigation-unsafe save action")
        actions[-1] = {
            "action": "click",
            "selector": "#save-button",
            "wait_for_navigation": True,
        }
        expected_actions = task["ground_truth"]["expected_actions"]
        if len(expected_actions) != len(actions) or expected_actions[-1] != "evaluate":
            raise ValueError(f"task {task_id} action contract changed")
        expected_actions[-1] = "click"
        task["trajectory"]["provenance"] = {
            "kind": "expert_repair",
            "repair": "wait for the product-save navigation through a browser click",
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
