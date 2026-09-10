from __future__ import annotations

import pytest

from scripts.propose_webarena_capture_target import propose_capture_target


def _suite(tag: str = "retrieve") -> dict:
    return {
        "tasks": [
            {
                "id": "webarena-58",
                "tags": [tag],
                "trajectory": {
                    "actions": [
                        {"action": "fill", "selector": "#search", "text": "cafe"},
                        {
                            "action": "click",
                            "target": {"by": "role", "role": "link", "name": "Cafe"},
                            "tab_index": 0,
                        },
                    ]
                },
            }
        ]
    }


def test_capture_target_inserts_browser_derived_output_before_click():
    proposal = propose_capture_target(
        _suite(), "webarena-58", action_index=2, output_name="cafe_name"
    )

    assert proposal["replace_actions"][1] == {
        "action": "get_text",
        "target": {"by": "role", "role": "link", "name": "Cafe"},
        "save_as": "cafe_name",
        "strip": True,
        "tab_index": 0,
    }
    assert proposal["replace_actions"][2]["action"] == "click"
    assert proposal["response_builder"]["shape"] == [{"$output": "cafe_name"}]


def test_capture_target_rejects_mutation_task():
    with pytest.raises(ValueError, match="retrieve task"):
        propose_capture_target(
            _suite("mutate"), "webarena-58", action_index=2, output_name="name"
        )
