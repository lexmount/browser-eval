from __future__ import annotations

import json
from pathlib import Path

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


def test_correction_shard_replaces_only_its_declared_rows(
    tmp_path: Path, monkeypatch
) -> None:
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
    monkeypatch.setattr(
        webarena_results, "validate_qualification_dataset", lambda *_: "qualified"
    )
    base = _write(
        tmp_path / "base.json",
        _receipt("formal", [1, 2], {1: "candidate-1", 2: "candidate-2-old"}),
    )
    correction = _write(
        tmp_path / "correction.json",
        _receipt(
            "formal_correction_shard", [2], {2: "candidate-2-new"}
        ),
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
