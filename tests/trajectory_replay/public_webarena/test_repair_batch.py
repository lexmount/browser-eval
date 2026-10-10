from __future__ import annotations

import pytest

from harness.trajectory_replay.public_webarena.qualification import build_proposal_suite


def _task(task_id):
    return {
        "id": task_id,
        "title": task_id,
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "provenance": {"kind": "official"},
            "actions": [{"action": "click", "selector": "#old"}],
        },
        "ground_truth": {
            "expected_actions": ["click"],
            "qualification": {"status": "pending"},
        },
    }


def _proposal(task_id):
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": [{"action": "click", "selector": "#new"}],
        "response_builder": {
            "kind": "null_after_verified_path",
            "task_type": "mutate",
        },
        "reason": "Observed navigation auto-wait blocks after the click completes.",
    }


def test_repair_batch_preserves_proposal_order_and_independent_provenance():
    suite = {"tasks": [_task("a"), _task("b")], "benchmark": {"name": "WebArena"}}

    result = build_proposal_suite(suite, [_proposal("b"), _proposal("a")])

    assert [task["id"] for task in result["tasks"]] == ["b", "a"]
    assert all(
        task["trajectory"]["provenance"]["kind"] == "codex_repair_probe"
        for task in result["tasks"]
    )
    assert result["benchmark"]["repair_probe_batch"]["task_ids"] == ["b", "a"]


def test_repair_batch_rejects_duplicate_task_ids():
    with pytest.raises(ValueError, match="unique"):
        build_proposal_suite({"tasks": [_task("a")]}, [_proposal("a"), _proposal("a")])
