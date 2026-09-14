from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.expert_seeds import (
    REPAIR_SCHEMA,
    build_expert_seed_manifest,
    merge_expert_seed_manifest,
)


def _base_manifest() -> dict:
    return {
        "schema": "lex_browser_use_replay_seeds/1",
        "counts": {
            "tasks": 3,
            "tasks_with_authoritative_success": 2,
            "replay_ready_tasks": 1,
            "success_with_failed_actions_tasks": 1,
            "all_methods_failed_tasks": 1,
            "successful_method_trajectories": 2,
            "clean_method_trajectories": 1,
        },
        "tasks": [
            {
                "task_id": 1,
                "candidates": [{"id": "existing-clean", "clean": True}],
                "canonical_candidate_id": "existing-clean",
                "replay_status": "ready",
            },
            {
                "task_id": 2,
                "candidates": [{"id": "existing-dirty", "clean": False}],
                "canonical_candidate_id": None,
                "replay_status": "success_contains_failed_actions",
            },
            {
                "task_id": 3,
                "candidates": [],
                "canonical_candidate_id": None,
                "replay_status": "all_methods_failed",
            },
        ],
    }


def _repair(task_id: int) -> dict:
    return {
        "task_id": task_id,
        "source": {"review": "root-cause-review-v1", "attempts_sha256": ["abc"]},
        "repair_basis": {
            "root_cause": "the autonomous run used the wrong route",
            "repair": "navigate to the contract route",
        },
        "tool_calls": [
            {"action": "navigate", "url": "http://gitlab.local/explore?visibility_level=20"},
            {"action": "snapshot"},
        ],
        "expected_response": {"task_type": "NAVIGATE", "status": "SUCCESS"},
    }


def test_merge_appends_expert_candidates_and_preserves_existing_candidates() -> None:
    base = _base_manifest()
    original = copy.deepcopy(base)
    spec = {"schema": REPAIR_SCHEMA, "repairs": [_repair(2), _repair(3)]}

    merged = merge_expert_seed_manifest(base, spec)
    tasks = {task["task_id"]: task for task in merged["tasks"]}

    assert base == original
    assert tasks[1] == original["tasks"][0]
    assert tasks[2]["candidates"][0] == original["tasks"][1]["candidates"][0]
    assert tasks[2]["canonical_candidate_id"] == "expert__task-2"
    assert tasks[3]["canonical_candidate_id"] == "expert__task-3"
    assert tasks[2]["replay_status"] == tasks[3]["replay_status"] == "ready"
    candidate = tasks[2]["candidates"][-1]
    assert candidate["candidate_kind"] == "expert_repair"
    assert candidate["source"]["details"] == spec["repairs"][0]["source"]
    assert candidate["repair_basis"] == spec["repairs"][0]["repair_basis"]
    assert candidate["expected_response"] == spec["repairs"][0]["expected_response"]
    assert candidate["recorded_response"] == candidate["expected_response"]
    assert merged["counts"]["replay_ready_tasks"] == 3
    assert merged["counts"]["success_with_failed_actions_tasks"] == 0
    assert merged["counts"]["all_methods_failed_tasks"] == 0
    assert merged["counts"]["expert_repair_candidates"] == 2
    assert merged["counts"]["successful_method_trajectories"] == 2


def test_merge_rejects_refs_at_any_depth() -> None:
    spec = {"schema": REPAIR_SCHEMA, "repairs": [_repair(2)]}
    spec["repairs"][0]["tool_calls"][0]["metadata"] = {"ref": "stale-ref"}

    with pytest.raises(ValueError, match="contains forbidden ref"):
        merge_expert_seed_manifest(_base_manifest(), spec)


@pytest.mark.parametrize(
    "call",
    [
        {"action": "wait", "timeout": 1000},
        {"action": "wait", "seconds": 1, "selector": "main"},
        {"action": "wait", "seconds": 0},
    ],
)
def test_merge_rejects_invalid_wait_arguments(call: dict) -> None:
    repair = _repair(2)
    repair["tool_calls"] = [call]

    with pytest.raises(ValueError, match="wait"):
        merge_expert_seed_manifest(
            _base_manifest(), {"schema": REPAIR_SCHEMA, "repairs": [repair]}
        )


def test_merge_refuses_to_replace_ready_candidate() -> None:
    with pytest.raises(ValueError, match="cannot replace ready task 1"):
        merge_expert_seed_manifest(
            _base_manifest(), {"schema": REPAIR_SCHEMA, "repairs": [_repair(1)]}
        )


def test_build_binds_exact_input_files(tmp_path: Path) -> None:
    base_path = tmp_path / "base.json"
    repairs_path = tmp_path / "repairs.json"
    base_path.write_text(json.dumps(_base_manifest()), encoding="utf-8")
    repairs_path.write_text(
        json.dumps({"schema": REPAIR_SCHEMA, "repairs": [_repair(2)]}), encoding="utf-8"
    )

    manifest = build_expert_seed_manifest(base_path, repairs_path)

    assert len(manifest["expert_repairs"]["base_manifest_sha256"]) == 64
    assert len(manifest["expert_repairs"]["repair_spec_sha256"]) == 64
    assert manifest["expert_repairs"]["candidate_count"] == 1
