from __future__ import annotations

import pytest

from scripts.propose_webarena_evaluator_fill_repair import (
    propose_evaluator_fill_repair,
)


def _suite(*, expected: str, target: str = "Title", actual: str = "Wrong"):
    return {
        "tasks": [
            {
                "id": "webarena-1",
                "title": "Create a post",
                "trajectory": {
                    "actions": [
                        {
                            "action": "fill",
                            "target": {"by": "label", "value": target},
                            "text": actual,
                        }
                    ]
                },
                "ground_truth": {
                    "response_builder": {
                        "kind": "null_after_verified_path",
                        "task_type": "mutate",
                    }
                },
                "source": {
                    "verified_evaluators": [
                        {
                            "evaluator": "NetworkEventEvaluator",
                            "expected": {"post_data": {"submission[title]": expected}},
                        }
                    ]
                },
            }
        ]
    }


def test_replaces_only_the_final_owned_fill():
    suite = _suite(expected="Exact title")
    suite["tasks"][0]["trajectory"]["actions"].insert(
        0,
        {
            "action": "fill",
            "target": {"by": "label", "value": "Title"},
            "text": "First draft",
        },
    )

    proposal = propose_evaluator_fill_repair(suite, "webarena-1")

    assert [action["text"] for action in proposal["replace_actions"]] == [
        "First draft",
        "Exact title",
    ]
    assert proposal["repair_audit"] == [
        {
            "field": "title",
            "action_index": 2,
            "previous": "Wrong",
            "replacement": "Exact title",
        }
    ]


def test_refuses_to_guess_a_missing_owned_field():
    suite = _suite(
        expected="http://old.example:8023/group/project",
        target="Body",
        actual="http://old.example:8023/group/project",
    )
    suite["tasks"][0]["source"]["verified_evaluators"][0]["expected"]["post_data"] = {
        "submission[url]": "__GITLAB__/group/project"
    }

    with pytest.raises(ValueError, match="unsupported judgment"):
        propose_evaluator_fill_repair(suite, "webarena-1")


def test_refuses_task_without_a_detected_mismatch():
    suite = _suite(expected="Exact title", actual="Exact title")

    with pytest.raises(ValueError, match="no exact evaluator fill mismatch"):
        propose_evaluator_fill_repair(suite, "webarena-1")
