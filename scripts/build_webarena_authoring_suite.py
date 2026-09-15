"""Build a warm-site, retrieve-only suite for deterministic trajectory authoring."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.suite import load_suite
from scripts.build_webarena_observation_suite import build_observation_suite


def build_authoring_suite(
    suite: dict[str, Any],
    qualification: dict[str, Any],
    *,
    site: str | None = None,
    target_timeout_ms: int = 5_000,
    task_ids: set[str] | None = None,
    task_types: set[str] | None = None,
    source_dispositions: set[str] | None = None,
) -> dict[str, Any]:
    if target_timeout_ms < 1:
        raise ValueError("authoring target timeout must be positive")
    allowed_task_types = task_types or {"retrieve"}
    if not allowed_task_types or not allowed_task_types <= {"retrieve", "navigate"}:
        raise ValueError("authoring task types must be retrieve and/or navigate")
    allowed_dispositions = source_dispositions or {"needs_trace_repair"}
    if not allowed_dispositions or not allowed_dispositions <= {
        "needs_trace_repair",
        "needs_response_annotation",
    }:
        raise ValueError("unsupported authoring source disposition")
    dispositions = {
        str(row.get("task_id")): row.get("disposition")
        for row in qualification.get("tasks") or []
    }
    selected_ids = []
    for task in suite.get("tasks") or []:
        task_id = str(task.get("id"))
        if dispositions.get(task_id) not in allowed_dispositions:
            continue
        if not allowed_task_types.intersection(task.get("tags") or []):
            continue
        sites = (task.get("environment") or {}).get("sites") or []
        if site is not None and site not in sites:
            continue
        if task_ids is not None and task_id not in task_ids:
            continue
        selected_ids.append(task_id)
    if task_ids is not None and set(selected_ids) != task_ids:
        missing = sorted(task_ids.difference(selected_ids))
        raise ValueError(f"requested authoring tasks are outside the eligible scope: {missing}")
    if not selected_ids:
        raise ValueError("no non-mutating trace repairs match the requested authoring scope")

    result = build_observation_suite(suite, selected_ids, output_name="authoring_page")
    source_by_id = {str(task.get("id")): task for task in suite.get("tasks") or []}
    for task in result["tasks"]:
        for action in task["trajectory"]["actions"]:
            if action.get("selector") is not None or isinstance(action.get("target"), dict):
                action.setdefault("timeout_ms", target_timeout_ms)
            if isinstance(action.get("target"), dict):
                action.setdefault("target_timeout_ms", target_timeout_ms)
        if "navigate" in (task.get("tags") or []):
            source_builder = (
                source_by_id[task["id"]].get("ground_truth") or {}
            ).get("response_builder")
            task["ground_truth"]["response_builder"] = deepcopy(
                source_builder
                or {
                    "kind": "null_after_verified_path",
                    "task_type": "navigate",
                }
            )
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "trajectory_authoring": {
            "non_canonical": True,
            "diagnostic_only": True,
            "source_dispositions": sorted(allowed_dispositions),
            "task_types": sorted(allowed_task_types),
            "site": site,
            "task_count": len(selected_ids),
            "diagnostic_target_timeout_ms": target_timeout_ms,
            "qualification_evidence": False,
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--site")
    parser.add_argument("--target-timeout-ms", type=int, default=5_000)
    parser.add_argument("--task-ids")
    parser.add_argument("--task-types", default="retrieve")
    parser.add_argument("--source-dispositions", default="needs_trace_repair")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_authoring_suite(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
        site=args.site,
        target_timeout_ms=args.target_timeout_ms,
        task_ids=(
            {item.strip() for item in args.task_ids.split(",") if item.strip()}
            if args.task_ids
            else None
        ),
        task_types={
            item.strip() for item in args.task_types.split(",") if item.strip()
        },
        source_dispositions={
            item.strip()
            for item in args.source_dispositions.split(",")
            if item.strip()
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
