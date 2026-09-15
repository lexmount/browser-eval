"""Combine independently declared one-task repair proposals into a validation batch."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.suite import load_suite
from scripts.build_webarena_repair_probe import build_probe


def build_batch(suite: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    if not proposals:
        raise ValueError("repair batch requires at least one proposal")
    task_ids = [str(item.get("task_id") or "") for item in proposals]
    if not all(task_ids) or len(set(task_ids)) != len(task_ids):
        raise ValueError("repair batch task IDs must be non-empty and unique")
    tasks = [build_probe(suite, proposal)["tasks"][0] for proposal in proposals]
    result = deepcopy(suite)
    result["tasks"] = tasks
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "repair_probe_batch": {
            "non_canonical": True,
            "harness_revalidation": False,
            "task_ids": task_ids,
            "task_count": len(task_ids),
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    proposals = [json.loads(path.read_text(encoding="utf-8")) for path in args.proposal]
    result = build_batch(suite, proposals)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
