from __future__ import annotations

from harness.trajectory_replay.public_webarena.qualification import build_observation_suite


def test_observation_suite_is_noncanonical_and_appends_visible_text_capture():
    suite = {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {"name": "WebArena"},
        "tasks": [
            {
                "id": "webarena-1",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "provenance": {"kind": "official"},
                    "actions": [{"action": "click", "selector": "#result"}],
                },
                "ground_truth": {
                    "expected_actions": ["click"],
                    "qualification": {"status": "pending"},
                },
            }
        ],
    }

    result = build_observation_suite(suite, ["webarena-1"])

    task = result["tasks"][0]
    assert task["trajectory"]["actions"][-1] == {
        "action": "snapshot",
        "selector": "body",
        "save_as": "repair_page",
        "max_chars": 40_000,
    }
    assert task["ground_truth"]["expected_actions"] == ["click", "snapshot"]
    assert task["ground_truth"]["response_builder"]["shape"] == [
        {"$output": "repair_page"}
    ]
    assert result["benchmark"]["response_observation_probe"]["non_canonical"] is True
