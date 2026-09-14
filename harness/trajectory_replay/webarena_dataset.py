"""Promote replay-proven Browser Use trajectories into the shared dataset."""

from __future__ import annotations

import copy
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


def _candidate_source(candidate: dict[str, Any]) -> dict[str, Any]:
    source = candidate.get("source") or {}
    if candidate.get("candidate_kind") == "expert_repair":
        return {
            "kind": "expert_repair_spec",
            "candidate_id": candidate["id"],
            "repair_spec_sha256": source["repair_spec_sha256"],
            "details": source["details"],
            "repair_basis": candidate["repair_basis"],
        }
    method = candidate["method"]
    return {
        "kind": "authoritative_successful_browser_use_execution",
        "candidate_id": candidate["id"],
        "model": method["model"],
        "browser": method["engine"],
        "execution": candidate["source_execution"],
        "events_sha256": source["events_sha256"],
        "response_sha256": source["response_sha256"],
        "evaluation_sha256": source["evaluation_sha256"],
    }


def build_verified_trajectory_dataset(
    seed_manifest_path: str | Path,
    replay_receipt_paths: list[str | Path],
    *,
    required_engines: tuple[str, ...] = ("chromium", "moli"),
    prior_dataset_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return compact trajectories that passed process and official evaluation gates."""

    seed_path = Path(seed_manifest_path).expanduser().resolve()
    seed_sha256 = _sha256(seed_path)
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
        if receipt.get("source_seed_manifest_sha256") != seed_sha256:
            raise ValueError(f"replay receipt is not bound to the selected seeds: {receipt_path}")
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
        correction_proofs = {
            (
                str(item["contract_correction_id"]),
                str(item.get("contract_correction_source_sha256") or ""),
                str(item.get("corrected_task_sha256") or ""),
            )
            for item in by_engine.values()
            if item.get("contract_correction_id")
        }
        if correction_proofs and (
            sum(bool(item.get("contract_correction_id")) for item in by_engine.values())
            != len(required_engines)
            or len(correction_proofs) > 1
            or any(not value for value in next(iter(correction_proofs)))
        ):
            raise ValueError(f"inconsistent replay contracts for {candidate_id}")
        correction_proof = next(iter(correction_proofs), None)
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
                "source": _candidate_source(candidate),
                "verification": {
                    "browsers": list(required_engines),
                    "process_status": "pass",
                    "evaluator_status": "pass",
                    "contract": (
                        {
                            "kind": "corrected_replay_contract",
                            "correction_id": correction_proof[0],
                            "correction_source_sha256": correction_proof[1],
                            "corrected_task_sha256": correction_proof[2],
                        }
                        if correction_proof
                        else {"kind": "official_task_contract"}
                    ),
                    "model_calls": 0,
                    "receipt_run_ids": sorted(receipt_runs_by_key[(task_id, candidate_id)] - {""}),
                },
            }
        )

    prior_dataset_sha256 = None
    if prior_dataset_path is not None:
        prior_path = Path(prior_dataset_path).expanduser().resolve()
        prior = _read_json(prior_path)
        if prior.get("schema") != SCHEMA:
            raise ValueError("unsupported prior trajectory dataset")
        if (
            prior.get("official_data_checksum")
            and seeds["source"].get("official_data_checksum")
            and prior["official_data_checksum"] != seeds["source"]["official_data_checksum"]
        ):
            raise ValueError("prior trajectory dataset uses a different official dataset")
        new_task_ids = {item["task_id"] for item in trajectories}
        prior_trajectories = copy.deepcopy(prior.get("trajectories") or [])
        overlap = sorted(new_task_ids & {item["task_id"] for item in prior_trajectories})
        if overlap:
            raise ValueError(f"prior trajectory dataset overlaps new proofs: {overlap}")
        trajectories.extend(prior_trajectories)
        trajectories.sort(key=lambda item: int(item["task_id"]))
        prior_dataset_sha256 = _sha256(prior_path)

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
            "evaluator_status": "pass",
            "model_calls": 0,
        },
        "provenance": {
            "seed_manifest_sha256": seed_sha256,
            "replay_receipts": receipts,
            "prior_dataset_sha256": prior_dataset_sha256,
        },
        "counts": {
            "verified_tasks": len({item["task_id"] for item in trajectories}),
            "verified_trajectories": len(trajectories),
        },
        "trajectories": trajectories,
    }
