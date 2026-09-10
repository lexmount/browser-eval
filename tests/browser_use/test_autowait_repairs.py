from __future__ import annotations

import pytest

from scripts.propose_webarena_autowait_repairs import propose_autowait_repair


def _fixtures(error):
    suite = {
        "tasks": [
            {
                "id": "webarena-1",
                "trajectory": {
                    "actions": [
                        {"action": "fill", "selector": "#title", "text": "Hello"},
                        {"action": "click", "selector": "#submit"},
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
    qualification = {
        "tasks": [
            {
                "task_id": "webarena-1",
                "disposition": "needs_trace_repair",
                "failed_step": {"index": 2, "action": "click", "error": error},
            }
        ]
    }
    return suite, qualification


def test_autowait_repair_changes_only_the_proven_failed_click():
    suite, qualification = _fixtures(
        "click action done; waiting for scheduled navigations to finish"
    )

    proposal = propose_autowait_repair(suite, qualification, "webarena-1")

    assert proposal["replace_actions"][0] == suite["tasks"][0]["trajectory"]["actions"][0]
    assert (
        proposal["replace_actions"][1]["accept_completed_action_navigation_timeout"] is True
    )
    assert (
        "accept_completed_action_navigation_timeout"
        not in suite["tasks"][0]["trajectory"]["actions"][1]
    )


def test_autowait_repair_rejects_an_unproven_timeout():
    suite, qualification = _fixtures("locator was not visible")

    with pytest.raises(ValueError, match="does not prove"):
        propose_autowait_repair(suite, qualification, "webarena-1")
