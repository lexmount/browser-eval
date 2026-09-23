"""Repair overlapping GitLab star trajectories using the proven target-state action."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite


def repair_suite(source_path: Path) -> dict:
    suite = json.loads(source_path.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in suite["tasks"]}
    template = tasks["webarena-524"]["trajectory"]["actions"][1]["script"]
    match = re.search(r"const specs=(\[.*?\]);const out=", template)
    if not match:
        raise ValueError("verified task 524 star action changed")
    specs = json.loads(match.group(1))
    for task_id, count in ((525, 4), (527, 1)):
        task = tasks[f"webarena-{task_id}"]
        action = task["trajectory"]["actions"][1]
        paths_match = re.search(r"const paths=(\[.*?\]);const out=", action["script"])
        if not paths_match or json.loads(paths_match.group(1)) != [
            spec["path"] for spec in specs[:count]
        ]:
            raise ValueError(f"task {task_id} no longer targets the expected repositories")
        action["script"] = template.replace(
            match.group(1), json.dumps(specs[:count], separators=(",", ":")), 1
        )
        task["trajectory"]["provenance"] = {
            "kind": "expert_repair",
            "repair": "use the task-524 target-state star action for the same ordered repository prefix",
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
    print(json.dumps({"repaired_task_ids": [525, 527]}))


if __name__ == "__main__":
    main()
