"""Non-score-bearing candidate contracts for the first current-retrieval batch."""

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


def _candidate(row: dict) -> dict:
    path = Path(__file__).resolve().parents[2] / row["candidate_path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == row["candidate_sha256"]
    return json.loads(path.read_text())


def test_batch_separates_contract_validity_from_official_score():
    assert MATRIX["score_bearing"] is False
    assert MATRIX["profile_bound"] is False
    assert MATRIX["official_scores_modified"] is False
    assert [row["task_id"] for row in MATRIX["rows"]] == MATRIX["population_task_ids"]
    assert len(MATRIX["rows"]) == 12

    for row in MATRIX["rows"]:
        assert len(row["receipt_sha256"]) == 64
        if row["level"] == "blocked":
            assert row["task_id"] in {243, 313}
            assert row["candidate_path"] is None
            assert row["contract_sha256"] is None
            assert row["calls_sha256"] is None
            assert row["official_score"] is None
            assert row["blocker"]
            continue
        candidate = _candidate(row)
        assert candidate["task_id"] == row["task_id"]
        assert candidate["retrieval_contract"]["task_id"] == row["task_id"]
        assert digest(candidate["retrieval_contract"]) == row["contract_sha256"]
        assert digest(candidate["tool_calls"]) == row["calls_sha256"]
        validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])
        if row["level"] == "acceptance_only":
            assert row["task_id"] == 108
            assert row["official_score"] is None
            assert row["blocker"]
        else:
            assert row["level"] == "diagnostic_official_pass"
            assert row["official_score"] == 1.0
            assert row["blocker"] is None


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
