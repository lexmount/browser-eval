"""Supply Magento's required checkout rule fields in three frozen trajectories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite


TASK_IDS = (700, 701, 703)


def repair_suite(source_path: Path) -> dict:
    suite = json.loads(source_path.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in suite["tasks"]}
    for task_id in TASK_IDS:
        task = tasks[f"webarena-{task_id}"]
        action = task["trajectory"]["actions"][-1]
        script = action.get("script", "")
        if (
            action.get("action") != "evaluate"
            or script.count('"body":{') != 1
            or '"simple_free_shipping"' in script
            or '"url":"/admin/sales_rule/promo_quote/save"' not in script
        ):
            raise ValueError(f"task {task_id} checkout rule action changed")
        fields = '"simple_free_shipping":"0",'
        if task_id == 703:
            fields += '"simple_action":"cart_fixed",'
        if task_id == 700:
            fields += '"is_active":"1",'
        action["script"] = script.replace('"body":{', '"body":{' + fields, 1)
        task["trajectory"]["provenance"] = {
            "kind": "expert_repair",
            "repair": "include Magento's required free-shipping field and the task's checkout discount mode",
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
