from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.trajectory_replay import webarena_results
from harness.trajectory_replay.webarena_replay import RECEIPT_SCHEMA


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _receipt(scope: str, task_ids: list[int], candidates: dict[int, str]) -> dict:
    engines = ["chromium", "moli"]
    attempts = [
        {
            "engine": engine,
            "task_id": task_id,
            "source_candidate_id": candidates[task_id],
            "status": "pass",
            "process_status": "pass",
            "evaluator_status": "pass",
        }
        for engine in engines
        for task_id in task_ids
    ]
    return {
        "schema": RECEIPT_SCHEMA,
        "run_id": scope,
        "execution_scope": scope,
        "status": "complete",
        "profile_sha256": "profile",
        "source_seed_manifest_sha256": "seeds",
        "qualification_dataset_sha256": "qualified",
        "task_ids": task_ids,
        "engines": engines,
        "method_teardown": {engine: True for engine in engines},
        "runtime": {"commit": scope},
        "sessions": [],
        "attempts": attempts,
        "physical_executions": [
            {"engine": row["engine"], "task_id": row["task_id"], "physical_execution": 1}
            for row in attempts
        ],
    }


def test_formal_receipt_cannot_promote_contradictory_pass_or_unsupported():
    receipt = _receipt("formal", [1], {1: "candidate-1"})
    for change in (
        {"process_status": "fail"},
        {"evaluator_status": "fail"},
        {"status": "unsupported"},
    ):
        row = receipt["attempts"][0]
        row.update(status="pass", process_status="pass", evaluator_status="pass")
        row.update(change)
        with pytest.raises(ValueError):
            webarena_results._validate_receipt(receipt, scope="formal", engines=receipt["engines"])


@pytest.mark.parametrize("legacy", [True, False])
def test_state_cohort_rejects_legacy_and_reverifies_bound_evidence(tmp_path, monkeypatch, legacy):
    from harness import webarena_state

    contract = SimpleNamespace(definition_sha256="new-state")
    plan = {
        "profile_path": "profile.json",
        "profile_sha256": "profile",
        "seed_manifest_path": "seeds.json",
        "seed_manifest_sha256": "seeds",
        "engines": ["chromium", "moli"],
        "task_ids": [1],
        "state_contracts": {1: contract},
        "profile": {"execution": {}},
        "seed_manifest": {"tasks": [{"task_id": 1, "canonical_candidate_id": "candidate-1"}]},
    }
    monkeypatch.setattr(webarena_results, "load_replay_plan", lambda *_: plan)
    monkeypatch.setattr(webarena_results, "validate_qualification_dataset", lambda *_: "qualified")
    checked = []

    def verify(row, actual_contract):
        assert actual_contract is contract
        checked.append(row["engine"])

    monkeypatch.setattr(webarena_state, "verify_attempt", verify)
    receipt = _receipt("formal", [1], {1: "candidate-1"})
    for row in receipt["attempts"]:
        row["run_id"] = receipt["run_id"]
        row["attempt_id"] = row["engine"] + "-attempt"
        if not legacy:
            row["state_contract_sha256"] = "new-state"
    path = _write(tmp_path / "base.json", receipt)
    arguments = dict(
        profile_path="profile",
        seed_manifest_path="seeds",
        qualified_dataset_path="qualified",
        base_receipt_path=path,
        correction_receipt_paths=[],
    )
    if legacy:
        with pytest.raises(ValueError, match="legacy result"):
            webarena_results.build_effective_results(**arguments)
        assert not checked
    else:
        webarena_results.build_effective_results(**arguments)
        assert checked == ["chromium", "moli"]


def test_planned_retrieval_run_binding_cannot_be_deleted_with_inline_receipt():
    receipt = _receipt("formal", [1], {1: "candidate-1"})
    for row in receipt["attempts"]:
        row["run_id"] = "old-run"
    with pytest.raises(ValueError, match="another cohort"):
        webarena_results._validate_receipt(
            receipt, scope="formal", engines=receipt["engines"], retrieval_task_ids={1}
        )
    for row in receipt["attempts"]:
        row["run_id"] = receipt["run_id"]
    assert (
        len(
            webarena_results._validate_receipt(
                receipt, scope="formal", engines=receipt["engines"], retrieval_task_ids={1}
            )
        )
        == 2
    )


@pytest.mark.parametrize("forgery", ["contradictory_pass", "cross_run"])
def test_effective_builder_rejects_forged_retrieval_in_old_base(tmp_path, monkeypatch, forgery):
    plan = {"engines": ["chromium", "moli"], "task_ids": [1], "retrieval_task_ids": [1]}
    monkeypatch.setattr(webarena_results, "load_replay_plan", lambda *_: plan)
    monkeypatch.setattr(
        webarena_results, "validate_qualification_dataset", lambda *_: "new-qualified"
    )
    receipt = _receipt("formal", [1], {1: "candidate-1"})
    receipt.update(
        profile_sha256="old-profile",
        source_seed_manifest_sha256="old-seeds",
        qualification_dataset_sha256="old-qualified",
    )
    for row in receipt["attempts"]:
        row.update(run_id="old-run" if forgery == "cross_run" else receipt["run_id"])
        if forgery == "contradictory_pass":
            row["process_status"] = "fail"
    path = _write(tmp_path / "base.json", receipt)
    with pytest.raises(ValueError, match="contradicts|another cohort"):
        webarena_results.build_effective_results(
            profile_path="profile",
            seed_manifest_path="seeds",
            qualified_dataset_path="qualified",
            base_receipt_path=path,
            correction_receipt_paths=[],
        )


def test_correction_shard_replaces_only_its_declared_rows(tmp_path: Path, monkeypatch) -> None:
    plan = {
        "profile_path": "profile.json",
        "profile_sha256": "profile",
        "seed_manifest_path": "seeds.json",
        "seed_manifest_sha256": "seeds",
        "task_ids": [1, 2],
        "engines": ["chromium", "moli"],
        "profile": {"execution": {"method_parallelism": 1}},
        "seed_manifest": {
            "tasks": [
                {"task_id": 1, "canonical_candidate_id": "candidate-1"},
                {"task_id": 2, "canonical_candidate_id": "candidate-2-new"},
            ]
        },
    }
    monkeypatch.setattr(webarena_results, "load_replay_plan", lambda *_: plan)
    monkeypatch.setattr(webarena_results, "validate_qualification_dataset", lambda *_: "qualified")
    base = _write(
        tmp_path / "base.json",
        _receipt("formal", [1, 2], {1: "candidate-1", 2: "candidate-2-old"}),
    )
    correction = _write(
        tmp_path / "correction.json",
        _receipt("formal_correction_shard", [2], {2: "candidate-2-new"}),
    )

    result = webarena_results.build_effective_results(
        profile_path="profile.json",
        seed_manifest_path="seeds.json",
        qualified_dataset_path="qualified.json",
        base_receipt_path=base,
        correction_receipt_paths=[correction],
    )

    assert result["counts"] == {
        "logical_results": 4,
        "physical_executions": 4,
        "passes": 4,
        "failures": 0,
    }
    rows = {(row["engine"], row["task_id"]): row for row in result["attempts"]}
    assert rows[("chromium", 1)]["source_candidate_id"] == "candidate-1"
    assert rows[("moli", 2)]["source_candidate_id"] == "candidate-2-new"
    assert [item["role"] for item in result["source_receipts"]] == [
        "base",
        "correction",
    ]
