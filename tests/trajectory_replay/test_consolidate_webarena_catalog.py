import hashlib

import pytest

from harness.trajectory_replay.catalog_consolidation import (
    _catalog_rows_by_id,
    _verified_run_directories,
)


def test_consolidation_rejects_inputs_changed_after_acceptance(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    progress = run / "progress.jsonl"
    progress.write_text('{"task_id":0,"status":"pass"}\n', encoding="utf-8")
    inputs = run / "inputs.json"
    inputs.write_text('{"catalog":"accepted"}\n', encoding="utf-8")
    progress_hash = hashlib.sha256(progress.read_bytes()).hexdigest()
    inputs_hash = hashlib.sha256(inputs.read_bytes()).hexdigest()
    ledger = {
        "runs": [
            {
                "progress_sha256": progress_hash,
                "inputs_sha256": inputs_hash,
            }
        ]
    }

    assert _verified_run_directories(ledger, tmp_path, {progress_hash}) == {progress_hash: run}
    inputs.write_text('{"catalog":"tampered"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="inputs changed"):
        _verified_run_directories(ledger, tmp_path, {progress_hash})


def test_consolidation_rejects_duplicate_ledger_run_bindings(tmp_path):
    row = {"progress_sha256": "same", "inputs_sha256": "hash"}
    with pytest.raises(ValueError, match="duplicate run bindings"):
        _verified_run_directories({"runs": [row, row]}, tmp_path, set())


def test_consolidation_indexes_non_contiguous_catalog_tasks_by_identity():
    rows = [{"task_id": 7}, {"task_id": 20}]
    assert _catalog_rows_by_id({"task_ids": [7, 20], "rows": rows}) == {7: rows[0], 20: rows[1]}


def test_consolidation_rejects_catalog_identity_mismatch():
    with pytest.raises(ValueError, match="declared order"):
        _catalog_rows_by_id({"task_ids": [7, 20], "rows": [{"task_id": 7}, {"task_id": 7}]})
