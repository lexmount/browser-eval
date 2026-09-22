"""Propose trajectories for unrecorded cases from the same official task template."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _transfer(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, list):
        return [_transfer(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _transfer(item, replacements) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    result = value
    for before, after in replacements:
        if before == result:
            result = after
        elif len(before) >= 3 and not before.isdigit():
            result = result.replace(before, after)
    return result


def build_candidates(
    official_path: Path,
    queue_path: Path,
    human_path: Path,
    curated_path: Path | None = None,
) -> dict:
    official = json.loads(official_path.read_text(encoding="utf-8"))
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    human = json.loads(human_path.read_text(encoding="utf-8"))
    if (
        queue.get("schema") != "lex_webarena_verified_full_queue/1"
        or queue.get("human_suite_sha256") != _sha256(human_path)
        or [row["task_id"] for row in queue["rows"]] != list(range(len(official)))
    ):
        raise ValueError("candidate inputs are not the frozen full-split queue")
    by_id = {row["task_id"]: row for row in official}
    sources: dict[int, list[tuple[dict, dict, str]]] = {}
    for task in human["tasks"]:
        source_id = int(task["id"].removeprefix("webarena-"))
        actions = (task.get("trajectory") or {}).get("actions") or []
        if actions:
            source = by_id[source_id]
            sources.setdefault(source["intent_template_id"], []).append((source, task, "human"))
    if curated_path is not None:
        curated = json.loads(curated_path.read_text(encoding="utf-8"))
        for trajectory in curated["trajectories"]:
            source_id = trajectory["task_id"]
            if queue["rows"][source_id]["status"] != "pass":
                continue
            actions = trajectory.get("tool_calls") or []
            if actions:
                source = by_id[source_id]
                sources.setdefault(source["intent_template_id"], []).append(
                    (source, trajectory, "curated")
                )
    candidates = []
    unmatched = []
    for row in queue["rows"]:
        if row["status"] != "needs_trajectory":
            continue
        target = by_id[row["task_id"]]
        needs_live_response = any(
            item.get("evaluator") == "AgentResponseEvaluator"
            and str((item.get("expected") or {}).get("task_type", "")).lower() == "retrieve"
            for item in target.get("eval") or []
        )
        choices = [
            (source, task, kind)
            for source, task, kind in sources.get(target["intent_template_id"], [])
            if source["sites"] == target["sites"] and (kind == "human" or not needs_live_response)
        ]
        if not choices:
            unmatched.append(row["task_id"])
            continue
        target_values = target.get("instantiation_dict") or {}
        choices.sort(
            key=lambda pair: (
                pair[2] == "human"
                and (pair[1].get("ground_truth") or {}).get("qualification", {}).get("status")
                == "eligible",
                sum(
                    pair[0].get("instantiation_dict", {}).get(key) == value
                    for key, value in target_values.items()
                ),
                -len(
                    pair[1]["trajectory"]["actions"]
                    if pair[2] == "human"
                    else pair[1]["tool_calls"]
                ),
                -pair[0]["task_id"],
            ),
            reverse=True,
        )
        source, task, kind = choices[0]
        source_values = source.get("instantiation_dict") or {}
        replacements = [
            (str(value), str(target_values[key]))
            for key, value in source_values.items()
            if key in target_values
            and isinstance(value, (str, int, float))
            and isinstance(target_values[key], (str, int, float))
            and value != target_values[key]
        ]
        replacements.extend(
            (str(old), str(new))
            for old, new in zip(source["start_urls"], target["start_urls"], strict=True)
            if old != new
        )
        source_actions = (
            task["trajectory"]["actions"] if kind == "human" else task["tool_calls"]
        )
        actions = _transfer(source_actions, replacements)
        candidates.append(
            {
                "task_id": row["task_id"],
                "source_task_id": source["task_id"],
                "source_kind": kind,
                "intent_template_id": target["intent_template_id"],
                "source_actions_sha256": _digest(source_actions),
                "actions_sha256": _digest(actions),
                "replacements": [{"from": old, "to": new} for old, new in replacements],
                "actions": actions,
            }
        )
    return {
        "schema": "lex_webarena_template_candidates/1",
        "official_dataset_sha256": _sha256(official_path),
        "queue_sha256": _sha256(queue_path),
        "human_suite_sha256": _sha256(human_path),
        "curated_trajectories_sha256": _sha256(curated_path) if curated_path else None,
        "candidates": candidates,
        "unmatched_task_ids": unmatched,
        "qualification": "pending_chromium_replay_and_official_evaluator",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-dataset", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--human-suite", type=Path, required=True)
    parser.add_argument("--curated-trajectories", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_candidates(
        args.official_dataset, args.queue, args.human_suite, args.curated_trajectories
    )
    if args.output.exists():
        raise ValueError("candidate output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "candidates": len(result["candidates"]),
                "unmatched": len(result["unmatched_task_ids"]),
                "sha256": _sha256(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
