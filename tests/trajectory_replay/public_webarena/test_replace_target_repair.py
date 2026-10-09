from __future__ import annotations

import pytest

from harness.trajectory_replay.public_webarena.qualification import propose_replace_target


def _suite() -> dict:
    return {
        "tasks": [
            {
                "id": "webarena-714",
                "trajectory": {
                    "actions": [
                        {"action": "click", "selector": "#old"},
                    ]
                },
                "ground_truth": {
                    "response_builder": {
                        "kind": "null_after_verified_path",
                        "task_type": "mutate",
                    }
                },
            }
        ]
    }


def test_replace_target_preserves_path_and_can_select_first_visible_match():
    proposal = propose_replace_target(
        _suite(),
        "webarena-714",
        action_index=1,
        target={"by": "role", "role": "button", "name": "Downvote", "exact": True},
        allow_many=True,
        reason="Use the first visible downvote control for the top submission.",
    )

    action = proposal["replace_actions"][0]
    assert "selector" not in action
    assert action["target"]["name"] == "Downvote"
    assert action["allow_many"] is True
    assert proposal["response_builder"]["task_type"] == "mutate"


def test_replace_target_rejects_non_targetable_action():
    suite = _suite()
    suite["tasks"][0]["trajectory"]["actions"][0] = {
        "action": "fill",
        "selector": "#old",
        "text": "x",
    }
    with pytest.raises(ValueError, match="targetable"):
        propose_replace_target(
            suite,
            "webarena-714",
            action_index=1,
            target={"by": "role", "role": "button", "name": "Downvote"},
            reason="repair",
        )
