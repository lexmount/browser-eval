"""Non-score-bearing current-retrieval candidates and frozen-prefix controls."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.retrieval_acceptance import (
    build_actual_response,
    digest,
    validate_contract,
)

ROOT = (
    Path(__file__).resolve().parents[2]
    / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates"
)
MATRIX = json.loads((ROOT / "qualification-matrix.json").read_text())
SEEDS = json.loads((ROOT / "recompiled-seed-bindings.json").read_text())
SEED_BY_TASK = {row["task_id"]: row for row in SEEDS["rows"]}


def _candidate(row: dict) -> dict:
    path = Path(__file__).resolve().parents[2] / row["candidate_path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["candidate_sha256"]
    return json.loads(path.read_text())


def _assert_current_seed_extension(row: dict, calls: list[dict]) -> None:
    """A claim of qualification must preserve every frozen action before observations."""
    assert row["level"] in {"contract_only", "accepted", "pass"}
    seed = SEED_BY_TASK[row["task_id"]]
    count = seed["canonical_action_count"]
    assert len(calls) > count
    assert digest(calls[:count]) == seed["canonical_calls_sha256"]
    assert all(
        action.get("action") in {"navigate", "wait", "evaluate", "get_text"}
        for action in calls[count:]
    )


def test_batch_separates_contract_validity_from_official_score():
    assert MATRIX["score_bearing"] is False
    assert MATRIX["profile_bound"] is False
    assert MATRIX["official_scores_modified"] is False
    assert [row["task_id"] for row in MATRIX["rows"]] == MATRIX["population_task_ids"]
    assert len(MATRIX["rows"]) == 21
    assert SEEDS["task_count"] == 37
    assert MATRIX["ready_90_sha256"] == SEEDS["ready_90_sha256"]
    assert (
        hashlib.sha256((ROOT / "recompiled-seed-bindings.json").read_bytes()).hexdigest()
        == MATRIX["seed_bindings_sha256"]
    )

    for row in MATRIX["rows"]:
        seed = SEED_BY_TASK[row["task_id"]]
        assert row["canonical_seed_calls_sha256"] == seed["canonical_calls_sha256"]
        assert row["canonical_action_count"] == seed["canonical_action_count"] or (
            row["canonical_action_count"] is None and row["candidate_path"] is None
        )
        assert row["task_intent_sha256"] == seed["intent_sha256"]
        assert row["level"] in {
            "diagnostic_only",
            "blocked",
            "contract_only",
            "accepted",
            "pass",
        }
        if row["level"] == "blocked":
            assert row["eligible_for_qualification"] is False
            assert row["candidate_path"] is None
            assert row["contract_sha256"] is None
            assert row["calls_sha256"] is None
            assert row["official_score"] is None
            assert row["blocker"]
            continue
        assert len(row["receipt_sha256"]) == 64
        candidate = _candidate(row)
        assert candidate["task_id"] == row["task_id"]
        assert candidate["retrieval_contract"]["task_id"] == row["task_id"]
        assert digest(candidate["retrieval_contract"]) == row["contract_sha256"]
        assert digest(candidate["tool_calls"]) == row["calls_sha256"]
        validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])
        assert row["canonical_prefix_preserved"] is (
            len(candidate["tool_calls"]) >= seed["canonical_action_count"]
            and digest(candidate["tool_calls"][: seed["canonical_action_count"]])
            == seed["canonical_calls_sha256"]
        )
        if row["level"] in {"contract_only", "accepted", "pass"}:
            assert row["eligible_for_qualification"] is True
            _assert_current_seed_extension(row, candidate["tool_calls"])
        else:
            assert row["eligible_for_qualification"] is False
        if row["level"] != "diagnostic_only":
            continue
        if row["task_id"] == 108:
            assert row["diagnostic_stage"] == "acceptance_only"
            assert row["task_id"] == 108
            assert row["official_score"] is None
            assert row["blocker"]
        else:
            assert row["diagnostic_stage"] == "official_evaluator"
            assert row["official_score"] == 1.0
            assert row["blocker"]


@pytest.mark.parametrize("task_id", [29, 66, 108, 163, 173, 196, 204, 314, 349, 784])
def test_every_candidate_rejects_changed_canonical_action(task_id: int):
    row = next(row for row in MATRIX["rows"] if row["task_id"] == task_id)
    candidate = _candidate(row)
    calls = copy.deepcopy(candidate["tool_calls"])
    calls[0]["candidate_regression_mutation"] = True
    with pytest.raises(ValueError, match="canonical actions"):
        validate_contract(candidate["retrieval_contract"], calls)


@pytest.mark.parametrize("field", ["scope", "completeness"])
def test_current_observation_must_independently_prove_scope_and_completeness(field: str):
    row = next(row for row in MATRIX["rows"] if row["task_id"] == 29)
    candidate = _candidate(row)
    calls = candidate["tool_calls"]
    contract = candidate["retrieval_contract"]
    source = contract["sources"][0]
    records = [{} for _ in calls]
    observation = {"scope": True, "completeness": True, "answer": "independent sample"}

    def run() -> dict:
        records[source["step"] - 1] = {
            "index": source["step"],
            "arguments": calls[source["step"] - 1],
            "status": "success",
            "tool_status": "success",
            "result": json.dumps(observation),
        }
        return build_actual_response(
            contract=contract,
            task_id=29,
            calls=calls,
            records=records,
            run_id="synthetic-run",
            attempt_id="synthetic-attempt",
        )

    green = run()
    assert green["status"] == "supported"
    assert green["actual_response"]["retrieved_data"] == ["independent sample"]
    observation[field] = False
    red = run()
    assert red["status"] == "insufficient"
    assert red["actual_response"] is None


@pytest.mark.parametrize("task_id", [66, 163])
def test_reviewed_v3_evidence_digest_is_required(task_id: int):
    row = next(row for row in MATRIX["rows"] if row["task_id"] == task_id)
    candidate = _candidate(row)
    contract = candidate["retrieval_contract"]
    assert contract["schema"].endswith("/3")
    assert contract["semantic_reference"]["browser_semantic_inference"] is False
    validate_contract(contract, candidate["tool_calls"])
    changed = copy.deepcopy(contract)
    changed["semantic_reference"]["evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="review evidence"):
        validate_contract(changed, candidate["tool_calls"])


def test_scope_false_and_retrospective_identity_review_are_not_promoted():
    rows = {row["task_id"]: row for row in MATRIX["rows"]}
    assert rows[313]["level"] == rows[243]["level"] == "blocked"
    assert "scope=false" in rows[313]["blocker"]
    assert "identity" in rows[243]["blocker"]


def test_frozen_prefix_gate_has_paired_positive_and_negative_controls():
    rows = {row["task_id"]: row for row in MATRIX["rows"]}
    positive = copy.deepcopy(rows[29])
    positive["level"] = "contract_only"
    calls = _candidate(rows[29])["tool_calls"]
    _assert_current_seed_extension(positive, calls)

    changed_prior_action = copy.deepcopy(calls)
    changed_prior_action[0]["regression_mutation"] = True
    with pytest.raises(AssertionError):
        _assert_current_seed_extension(positive, changed_prior_action)

    changed_tail = copy.deepcopy(calls)
    changed_tail[-1] = {"action": "click", "selector": "button"}
    with pytest.raises(AssertionError):
        _assert_current_seed_extension(positive, changed_tail)

    prior_action_replaced = copy.deepcopy(rows[108])
    prior_action_replaced["level"] = "accepted"
    with pytest.raises(AssertionError):
        _assert_current_seed_extension(prior_action_replaced, _candidate(rows[108])["tool_calls"])

    blocked = copy.deepcopy(rows[313])
    blocked["level"] = "pass"
    with pytest.raises(AssertionError):
        _assert_current_seed_extension(blocked, [])


def test_next_batch_proposals_stay_blocked_without_current_page_qualification():
    rows = {row["task_id"]: row for row in MATRIX["rows"]}
    new_ids = {50, 77, 94, 183, 188, 198, 231, 320, 386}
    assert all(rows[task_id]["level"] == "blocked" for task_id in new_ids)
    assert all(rows[task_id]["official_score"] is None for task_id in new_ids)
    assert all(rows[task_id]["candidate_path"] is None for task_id in new_ids)
    for task_id in {50, 94, 188, 198, 231, 320}:
        assert len(rows[task_id]["proposal_sha256"]) == 64
        assert len(rows[task_id]["proposed_contract_sha256"]) == 64
        assert len(rows[task_id]["proposed_calls_sha256"]) == 64
    assert {task_id for task_id in new_ids if rows[task_id]["canonical_prefix_preserved"]} == {
        198,
        320,
    }
    assert all(rows[task_id]["proposal_sha256"] is None for task_id in {77, 183, 386})
