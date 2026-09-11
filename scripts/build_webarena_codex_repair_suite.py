"""Combine validated Codex proposals into one non-canonical replay suite."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.browser_use.suite import load_suite
from scripts.build_webarena_repair_probe import build_probe


def build_suite(
    suite: dict[str, Any], proposal_paths: Iterable[str | Path]
) -> dict[str, Any]:
    paths = [Path(path) for path in proposal_paths]
    if not paths:
        raise ValueError("at least one repair proposal is required")
    proposals = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    task_ids = [str(proposal.get("task_id") or "") for proposal in proposals]
    if "" in task_ids or len(task_ids) != len(set(task_ids)):
        raise ValueError("repair proposals must have unique non-empty task IDs")

    repaired_tasks = []
    for proposal in proposals:
        probe = build_probe(suite, proposal)
        repaired_tasks.append(deepcopy(probe["tasks"][0]))

    result = deepcopy(suite)
    result["tasks"] = repaired_tasks
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "codex_repair_validation": {
            "non_canonical": True,
            "task_count": len(repaired_tasks),
            "task_ids": task_ids,
            "qualification_required_passes": 2,
            "proposal_effect": "none_until_both_qualification_passes_succeed",
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_suite(
        json.loads(args.suite.read_text(encoding="utf-8")), args.proposal
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
