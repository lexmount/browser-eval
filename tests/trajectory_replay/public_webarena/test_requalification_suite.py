from __future__ import annotations

import pytest

from harness.trajectory_replay.public_webarena.qualification import build_requalification_suite


def _task(task_id: str, status: str) -> dict:
    return {
        "id": task_id,
        "ground_truth": {"qualification": {"status": status, "reason": "prior"}},
    }


def test_requalification_reopens_only_included_candidates():
    suite = {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {"id": "public"},
        "tasks": [_task("a", "eligible"), _task("b", "skipped")],
    }
    scope = {
        "schema": "lex_browser_use_native_scope/1",
        "included_task_ids": ["a"],
        "excluded_tasks": [{"task_id": "b", "reason": "architecture"}],
    }

    result = build_requalification_suite(suite, scope, predecessor_sha256="f" * 64)

    assert result["tasks"][0]["ground_truth"]["qualification"]["status"] == "pending"
    assert result["tasks"][1]["ground_truth"]["qualification"] == {
        "status": "skipped",
        "reason": "prior",
    }
    assert result["benchmark"]["requalification"] == {
        "predecessor_suite_sha256": "f" * 64,
        "included_count": 1,
        "excluded_count": 1,
        "previous_status_counts": {"eligible": 1, "skipped": 1},
        "trajectory_policy": "preserve every current canonical action and annotation",
    }


def test_requalification_requires_an_exact_scope_partition():
    suite = {
        "schema": "lex_browser_use_tasks/4",
        "tasks": [_task("a", "eligible")],
    }
    scope = {
        "schema": "lex_browser_use_native_scope/1",
        "included_task_ids": [],
        "excluded_tasks": [],
    }

    with pytest.raises(ValueError, match="partition"):
        build_requalification_suite(suite, scope, predecessor_sha256="f" * 64)
