from __future__ import annotations

import pytest

from harness.trajectory_replay.public_webarena.qualification import (
    propose_autowait_repair,
    task_contract_sha256,
)


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
    qualification["tasks"][0]["task_contract_sha256"] = task_contract_sha256(suite["tasks"][0])
    return suite, qualification


def test_autowait_repair_changes_only_the_proven_failed_click():
    suite, qualification = _fixtures(
        "click action done; waiting for scheduled navigations to finish"
    )

    qualification["tasks"][0]["task_contract_sha256"] = task_contract_sha256(suite["tasks"][0])
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


def test_autowait_repair_can_defer_enter_navigation_to_next_declared_target():
    suite, qualification = _fixtures(
        'Locator.press timed out; locator resolved; elementHandle.press("Enter")'
    )
    suite["tasks"][0]["trajectory"]["actions"] = [
        {
            "action": "press",
            "target": {"by": "label", "value": "Search query"},
            "key": "Enter",
        },
        {"action": "click", "target": {"by": "role", "role": "link", "name": "Result"}},
    ]
    qualification["tasks"][0]["failed_step"].update({"index": 1, "action": "press"})

    qualification["tasks"][0]["task_contract_sha256"] = task_contract_sha256(suite["tasks"][0])
    proposal = propose_autowait_repair(suite, qualification, "webarena-1")

    assert proposal["replace_actions"][0]["no_wait_after"] is True
    assert "no_wait_after" not in proposal["replace_actions"][1]


def test_autowait_rejects_trace_for_a_different_action():
    suite, qualification = _fixtures(
        "click action done; waiting for scheduled navigations to finish"
    )
    qualification["tasks"][0]["failed_step"]["index"] = 1
    with pytest.raises(ValueError, match="does not prove"):
        propose_autowait_repair(suite, qualification, "webarena-1")


def test_enter_trace_cannot_modify_a_different_key():
    suite, qualification = _fixtures('elementHandle.press("Enter")')
    suite["tasks"][0]["trajectory"]["actions"] = [
        {"action": "press", "selector": "#query", "key": "Escape"},
        {"action": "click", "target": {"by": "role", "role": "link", "name": "Result"}},
    ]
    qualification["tasks"][0]["failed_step"].update(index=1, action="press")
    qualification["tasks"][0]["task_contract_sha256"] = task_contract_sha256(suite["tasks"][0])
    with pytest.raises(ValueError, match="does not prove"):
        propose_autowait_repair(suite, qualification, "webarena-1")


@pytest.mark.parametrize("unbound", [False, True])
def test_autowait_requires_current_bound_qualification(unbound):
    suite, qualification = _fixtures(
        "click action done; waiting for scheduled navigations to finish"
    )
    if unbound:
        qualification["tasks"][0].pop("task_contract_sha256")
    else:
        suite["tasks"][0]["trajectory"]["actions"][1]["selector"] = "#other-submit"
    with pytest.raises(ValueError, match="regenerate the qualification queue"):
        propose_autowait_repair(suite, qualification, "webarena-1")
