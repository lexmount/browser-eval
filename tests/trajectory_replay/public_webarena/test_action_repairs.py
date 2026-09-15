from __future__ import annotations

import pytest

from scripts.propose_webarena_action_repair import propose_action_repair


def _suite():
    return {
        "tasks": [
            {
                "id": "webarena-1",
                "trajectory": {
                    "actions": [
                        {"action": "click", "selector": "#open"},
                        {"action": "fill", "selector": "#title", "text": "wrong"},
                        {"action": "click", "selector": "#save"},
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


def test_guarded_splice_replaces_only_declared_actions():
    source = _suite()["tasks"][0]["trajectory"]["actions"][1]
    config = {
        "schema": "lex_browser_use_action_repair/1",
        "task_id": "webarena-1",
        "reason": "The official trace fills the wrong exact title.",
        "edits": [
            {
                "start_index": 2,
                "expected_actions": [source],
                "replacement_actions": [
                    {"action": "fill", "selector": "#title", "text": "right"}
                ],
            }
        ],
    }

    proposal = propose_action_repair(_suite(), config)

    assert [action.get("text") for action in proposal["replace_actions"]] == [
        None,
        "right",
        None,
    ]
    assert proposal["repair_audit"][0]["start_index"] == 2


def test_guard_rejects_source_drift():
    config = {
        "schema": "lex_browser_use_action_repair/1",
        "task_id": "webarena-1",
        "reason": "Repair",
        "edits": [
            {
                "start_index": 2,
                "expected_actions": [
                    {"action": "fill", "selector": "#title", "text": "stale"}
                ],
                "replacement_actions": [],
            }
        ],
    }

    with pytest.raises(ValueError, match="source actions changed"):
        propose_action_repair(_suite(), config)


def test_hashed_guard_supports_large_exact_splices():
    config = {
        "schema": "lex_browser_use_action_repair/1",
        "task_id": "webarena-1",
        "reason": "Replace the complete proven-wrong workflow.",
        "edits": [
            {
                "start_index": 1,
                "delete_count": 3,
                "expected_actions_sha256": (
                    "754fbe4b216c83a077b02b0a9a9193fc37785dacaed6d5243ed878ae9c106644"
                ),
                "replacement_actions": [
                    {"action": "click", "selector": "#correct"}
                ],
            }
        ],
    }

    proposal = propose_action_repair(_suite(), config)

    assert proposal["replace_actions"] == [{"action": "click", "selector": "#correct"}]


def test_edits_must_be_ordered_and_non_overlapping():
    actions = _suite()["tasks"][0]["trajectory"]["actions"]
    config = {
        "schema": "lex_browser_use_action_repair/1",
        "task_id": "webarena-1",
        "reason": "Repair",
        "edits": [
            {
                "start_index": 2,
                "expected_actions": actions[1:3],
                "replacement_actions": actions[1:3],
            },
            {
                "start_index": 3,
                "expected_actions": actions[2:3],
                "replacement_actions": actions[2:3],
            },
        ],
    }

    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        propose_action_repair(_suite(), config)
