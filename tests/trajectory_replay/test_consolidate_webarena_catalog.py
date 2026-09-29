import hashlib
import json

import pytest

from harness.trajectory_replay.catalog_consolidation import (
    _catalog_rows_by_id,
    _load_bound_action_overrides,
    _source_action_overrides_path,
    _target_action_amendments,
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


def _action_overrides(tmp_path):
    path = tmp_path / "action-overrides.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_replay_action_overrides/1",
                "amendments": [
                    {
                        "task_id": 7,
                        "action_index": 1,
                        "precondition_action_sha256": "a" * 64,
                        "set": {"wait_for_selector_hidden": ".loading-mask"},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("bound_hash", [None, "invalid", "a" * 64])
def test_consolidation_can_use_catalog_embedded_historical_overrides(tmp_path, bound_hash):
    inputs = {"run": {"action_overrides_sha256": bound_hash}}
    assert _load_bound_action_overrides(inputs, []) == {}


def test_consolidation_accepts_the_hash_bound_external_action_overrides(tmp_path):
    path, digest = _action_overrides(tmp_path)
    sources = _load_bound_action_overrides({"run": {"action_overrides_sha256": digest}}, [path])
    assert sources[digest][0] == path
    assert set(sources[digest][1]) == {7}


def test_consolidation_maps_multiple_run_override_versions_by_digest(tmp_path):
    first, first_digest = _action_overrides(tmp_path)
    second = tmp_path / "action-overrides-v2.json"
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["amendments"][0]["task_id"] = 8
    second.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    second_digest = hashlib.sha256(second.read_bytes()).hexdigest()

    sources = _load_bound_action_overrides(
        {
            "first": {"action_overrides_sha256": first_digest},
            "second": {"action_overrides_sha256": second_digest},
        },
        [first, second],
    )
    assert set(sources) == {first_digest, second_digest}

    partial = _load_bound_action_overrides(
        {
            "first": {"action_overrides_sha256": first_digest},
            "second": {"action_overrides_sha256": second_digest},
        },
        [first],
    )
    assert set(partial) == {first_digest}


def test_consolidation_rejects_run_and_catalog_override_hash_mismatch(tmp_path):
    path, digest = _action_overrides(tmp_path)
    sources = {digest: (path, {7: []})}
    with pytest.raises(ValueError, match="binding differs from its source catalog"):
        _source_action_overrides_path(
            {"action_overrides_sha256": digest},
            {"action_overrides_sha256": "b" * 64},
            sources,
        )


def test_consolidation_rejects_target_amendment_not_used_by_accepted_task():
    new_amendment = {
        "task_id": 7,
        "action_index": 1,
        "precondition_action_sha256": "a" * 64,
        "set": {"wait_for_selector_hidden": ".loading-mask"},
    }
    with pytest.raises(ValueError, match="target action overrides differ"):
        _target_action_amendments(7, [], {7: [new_amendment]})
    assert _target_action_amendments(7, [new_amendment], {7: [new_amendment]}) == [new_amendment]
