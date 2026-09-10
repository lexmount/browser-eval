"""Convert all downloaded WebArena human traces to the common task-suite schema."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from harness.browser_use.webarena import import_playwright_trace


def _task_type(row: dict[str, Any]) -> str:
    evaluators = row["verified_evaluators"]
    response = next(item for item in evaluators if item["evaluator"] == "AgentResponseEvaluator")
    return str(response["expected"]["task_type"])


def _difficulty(step_count: int, lower: int, upper: int) -> str:
    if step_count <= lower:
        return "simple"
    if step_count <= upper:
        return "medium"
    return "complex"


def _tertile_bounds(counts: list[int]) -> tuple[int, int]:
    ordered = sorted(counts)
    return ordered[(len(ordered) - 1) // 3], ordered[(len(ordered) - 1) * 2 // 3]


def build_suite(catalog: dict[str, Any], trace_dir: Path) -> dict[str, Any]:
    imported: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in catalog["traces"]:
        trace_path = trace_dir / row["file_name"]
        if not trace_path.is_file():
            raise FileNotFoundError(f"missing trace: {trace_path}")
        imported.append((row, import_playwright_trace(trace_path, strict=True)))
    lower, upper = _tertile_bounds([len(trace["actions"]) for _, trace in imported])
    action_counts = Counter(
        str(action["action"])
        for _, trajectory in imported
        for action in trajectory["actions"]
    )
    recovered_input_counts = Counter()
    for _, trajectory in imported:
        for action in trajectory["actions"]:
            source = action.get("source") or {}
            if source.get("recovered_manual_input_from_snapshot"):
                recovered_input_counts["manual_typing"] += 1
            if source.get("recovered_from_input_snapshot"):
                recovered_input_counts["clipboard_paste"] += 1

    tasks: list[dict[str, Any]] = []
    for row, trajectory in imported:
        actions = trajectory["actions"]
        task_type = _task_type(row)
        ground_truth: dict[str, Any] = {
            "oracle": "webarena_verified",
            "expected_actions": [action["action"] for action in actions],
            "reference_provenance": "official_human_recording",
            "qualification": {
                "status": "pending",
                "reason": (
                    "official recorded does not imply successful; replay and evaluator "
                    "validation are required"
                ),
            },
        }
        if task_type != "retrieve":
            ground_truth["response_builder"] = {
                "kind": "null_after_verified_path",
                "task_type": task_type,
            }
        tasks.append(
            {
                "id": f"webarena-{row['task_id']}",
                "title": row["verified_intent"],
                "difficulty": _difficulty(len(actions), lower, upper),
                "tags": [
                    "public-benchmark",
                    "webarena",
                    task_type,
                    *[f"site:{site}" for site in row["sites"]],
                ],
                "trajectory": trajectory,
                "assertions": [],
                "evaluator": {
                    "kind": "webarena_verified",
                    "task_id": row["task_id"],
                    "revision": row["verified_revision"],
                },
                "environment": {
                    "kind": "webarena_verified",
                    "sites": row["sites"],
                    "start_urls": row["start_urls"],
                    "reset_required": True,
                },
                "ground_truth": ground_truth,
                "source": {
                    "kind": "public_benchmark",
                    "url": row["download_url"],
                    "benchmark_url": catalog["source"]["folder_url"],
                    "webarena_commit": catalog["source"]["webarena_commit"],
                    "webarena_verified_commit": catalog["source"][
                        "webarena_verified_commit"
                    ],
                    "intent_template_id": row["intent_template_id"],
                    "verified_evaluators": row["verified_evaluators"],
                },
            }
        )
    return {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {
            "id": "public-webarena-human-trajectories",
            "trace_count": len(tasks),
            "selection": "all official human recordings",
            "step_count_tertiles": {"simple_max": lower, "medium_max": upper},
            "structural_import": {
                "action_count": sum(action_counts.values()),
                "action_counts": dict(sorted(action_counts.items())),
                "recovered_input_counts": dict(sorted(recovered_input_counts.items())),
                "unmapped_api_counts": {},
                "zero_action_task_ids": [
                    row["task_id"]
                    for row, trajectory in imported
                    if not trajectory["actions"]
                ],
                "replay_qualified_count": 0,
            },
            "qualification_required": True,
        },
        "tasks": tasks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-traces.json"),
    )
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if catalog.get("count") != 179:
        raise ValueError("catalog must contain exactly 179 traces")
    suite = build_suite(catalog, args.trace_dir)
    suite["benchmark"]["catalog_sha256"] = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(suite, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
