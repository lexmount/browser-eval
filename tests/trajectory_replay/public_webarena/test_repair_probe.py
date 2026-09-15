from __future__ import annotations

import pytest

from scripts.build_webarena_repair_probe import build_probe


def _suite():
    return {
        "schema": "lex_browser_use_suite/4",
        "benchmark": {},
        "tasks": [
            {
                "id": "webarena-1",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "provenance": {"kind": "official"},
                    "actions": [{"action": "click", "selector": "button"}],
                },
                "ground_truth": {
                    "expected_actions": ["click"],
                    "qualification": {"status": "pending"},
                },
            }
        ],
    }


def test_build_probe_appends_observations_without_mutating_source():
    suite = _suite()
    result = build_probe(
        suite,
        {
            "schema": "lex_browser_use_repair_proposal/1",
            "task_id": "webarena-1",
            "reason": "Observe the value reached by the official path.",
            "append_actions": [
                {"action": "get_text", "selector": "h1", "save_as": "heading"}
            ],
            "response_builder": {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [{"$output": "heading"}],
            },
        },
    )

    assert suite["tasks"][0]["trajectory"]["actions"] == [
        {"action": "click", "selector": "button"}
    ]
    assert [row["action"] for row in result["tasks"][0]["trajectory"]["actions"]] == [
        "click",
        "get_text",
    ]
    assert result["tasks"][0]["trajectory"]["provenance"]["kind"] == "codex_repair_probe"
    assert result["benchmark"]["repair_probe"]["non_canonical"] is True


def test_build_probe_rejects_incomplete_proposal():
    with pytest.raises(ValueError, match="requires exactly one"):
        build_probe(
            _suite(),
            {
                "schema": "lex_browser_use_repair_proposal/1",
                "task_id": "webarena-1",
            },
        )


def test_build_probe_can_replace_a_stale_recorded_path():
    suite = _suite()
    result = build_probe(
        suite,
        {
            "schema": "lex_browser_use_repair_proposal/1",
            "task_id": "webarena-1",
            "reason": "Replace a recorded locator that no longer exists.",
            "replace_actions": [
                {"action": "get_text", "selector": "main h1", "save_as": "heading"}
            ],
            "response_builder": {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [{"$output": "heading"}],
            },
        },
    )

    assert result["tasks"][0]["trajectory"]["actions"] == [
        {"action": "get_text", "selector": "main h1", "save_as": "heading"}
    ]
    assert result["tasks"][0]["ground_truth"]["expected_actions"] == ["get_text"]


def test_build_probe_rejects_append_and_replace_together():
    with pytest.raises(ValueError, match="exactly one"):
        build_probe(
            _suite(),
            {
                "schema": "lex_browser_use_repair_proposal/1",
                "task_id": "webarena-1",
                "reason": "Ambiguous proposal.",
                "append_actions": [{"action": "click", "selector": "a"}],
                "replace_actions": [{"action": "click", "selector": "b"}],
                "response_builder": {
                    "kind": "observed_shape",
                    "task_type": "retrieve",
                    "shape": [{"$output": "heading"}],
                },
            },
        )


def test_build_probe_can_revalidate_an_unchanged_trajectory_after_harness_fix():
    suite = _suite()
    result = build_probe(
        suite,
        {
            "schema": "lex_browser_use_repair_proposal/1",
            "task_id": "webarena-1",
            "reason": "Revalidate after fixing incomplete HAR request headers.",
            "harness_revalidation": True,
        },
    )

    task = result["tasks"][0]
    assert task["trajectory"]["actions"] == suite["tasks"][0]["trajectory"]["actions"]
    assert "response_builder" not in task["ground_truth"]
    assert task["trajectory"]["provenance"]["kind"] == "harness_revalidation_probe"
    assert result["benchmark"]["repair_probe"]["harness_revalidation"] is True


def test_build_probe_rejects_changes_in_harness_revalidation():
    with pytest.raises(ValueError, match="cannot modify"):
        build_probe(
            _suite(),
            {
                "schema": "lex_browser_use_repair_proposal/1",
                "task_id": "webarena-1",
                "reason": "Invalid mixed proposal.",
                "harness_revalidation": True,
                "append_actions": [{"action": "click", "selector": "a"}],
            },
        )
