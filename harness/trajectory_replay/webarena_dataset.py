"""Promote replay-proven Browser Use trajectories into the shared dataset."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "lex_webarena_verified_trajectory_dataset/1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_verified_trajectory_dataset(
    seed_manifest_path: str | Path,
    replay_receipt_paths: list[str | Path],
    *,
    required_engines: tuple[str, ...] = ("chromium", "moli"),
) -> dict[str, Any]:
    """Return compact trajectories that passed process and official evaluation gates."""

    seed_path = Path(seed_manifest_path).expanduser().resolve()
    seeds = _read_json(seed_path)
    if seeds.get("schema") != "lex_browser_use_replay_seeds/1":
        raise ValueError("unsupported replay seed manifest")
    seed_tasks = {int(item["task_id"]): item for item in seeds.get("tasks") or []}

    attempts_by_key: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    receipt_runs_by_key: dict[tuple[int, str], set[str]] = defaultdict(set)
    receipts: list[dict[str, str]] = []
    for raw_path in replay_receipt_paths:
        receipt_path = Path(raw_path).expanduser().resolve()
        receipt = _read_json(receipt_path)
        if receipt.get("schema") != "lex_webarena_trajectory_replay_canary/1":
            raise ValueError(f"unsupported replay receipt: {receipt_path}")
        receipts.append({"sha256": _sha256(receipt_path)})
        for attempt in receipt.get("attempts") or []:
            key = (int(attempt["task_id"]), str(attempt["source_candidate_id"]))
            attempts_by_key[key].append(attempt)
            receipt_runs_by_key[key].add(str(receipt.get("run_id") or ""))

    trajectories: list[dict[str, Any]] = []
    for (task_id, candidate_id), attempts in sorted(attempts_by_key.items()):
        task = seed_tasks.get(task_id)
        if task is None:
            raise ValueError(f"receipt references task missing from seeds: {task_id}")
        candidate = next(
            (item for item in task.get("candidates") or [] if item.get("id") == candidate_id),
            None,
        )
        if candidate is None:
            raise ValueError(f"receipt references candidate missing from seeds: {candidate_id}")
        by_engine: dict[str, dict[str, Any]] = {}
        for item in attempts:
            engine = str(item["engine"])
            if engine in by_engine:
                raise ValueError(f"duplicate replay proof for {candidate_id}/{engine}")
            by_engine[engine] = item
        if set(by_engine) != set(required_engines):
            continue
        if any(
            item.get("process_status") != "pass"
            or item.get("evaluator_status") != "pass"
            or int(item.get("completed_action_count") or 0) != int(candidate["action_count"])
            for item in by_engine.values()
        ):
            continue
        trajectories.append(
            {
                "id": f"webarena-verified-{task_id}-trajectory-1",
                "task_id": task_id,
                "task": {
                    key: task[key]
                    for key in (
                        "intent",
                        "intent_template_id",
                        "revision",
                        "sites",
                        "start_urls",
                    )
                    if key in task
                },
                "tool_calls": candidate["tool_calls"],
                "evaluation_input": candidate["recorded_response"],
                "source": {
                    "kind": "authoritative_successful_browser_use_execution",
                    "candidate_id": candidate_id,
                    "model": candidate["method"]["model"],
                    "browser": candidate["method"]["engine"],
                    "execution": candidate["source_execution"],
                    "events_sha256": candidate["source"]["events_sha256"],
                    "response_sha256": candidate["source"]["response_sha256"],
                    "evaluation_sha256": candidate["source"]["evaluation_sha256"],
                },
                "verification": {
                    "browsers": list(required_engines),
                    "process_status": "pass",
                    "official_evaluator_status": "pass",
                    "model_calls": 0,
                    "receipt_run_ids": sorted(receipt_runs_by_key[(task_id, candidate_id)] - {""}),
                },
            }
        )

    return {
        "schema": SCHEMA,
        "benchmark": seeds["source"].get("benchmark") or "WebArena-Verified",
        "benchmark_version": seeds["source"].get("benchmark_version") or None,
        "official_data_checksum": seeds["source"].get("official_data_checksum") or None,
        "selection_sha256": seeds["source"]["selection_sha256"],
        "semantics": {
            "trajectory_is_one_verified_feasible_path": True,
            "trajectory_is_the_only_correct_path": False,
            "browser_use_scoring_authority": "official task outcome contract",
            "browser_use_agent_visibility": "hidden",
            "trajectory_replay_role": "fixed BrowserTool action input",
            "evaluation_input_role": "official evaluator input only; never an Agent prompt",
        },
        "promotion_policy": {
            "required_browsers": list(required_engines),
            "process_status": "pass",
            "official_evaluator_status": "pass",
            "model_calls": 0,
        },
        "provenance": {
            "seed_manifest_sha256": _sha256(seed_path),
            "replay_receipts": receipts,
        },
        "counts": {
            "verified_tasks": len({item["task_id"] for item in trajectories}),
            "verified_trajectories": len(trajectories),
        },
        "trajectories": trajectories,
    }
