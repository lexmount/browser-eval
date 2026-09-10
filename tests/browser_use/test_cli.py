from __future__ import annotations

import hashlib
import json

import pytest

from harness.browser_use.cli import qualification_lock, task_ids_from_scope


def test_qualification_lock_prevents_parallel_runs_in_one_evidence_root(tmp_path):
    first = tmp_path / "qualification" / "shopping-0-of-1"
    second = tmp_path / "qualification" / "reddit-0-of-1"

    with qualification_lock(first):
        with pytest.raises(RuntimeError, match="another qualification process"):
            with qualification_lock(second):
                pass

    with qualification_lock(second):
        pass


def test_native_scope_batch_is_bound_to_exact_suite(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text('{"tasks": []}\n')
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_native_scope/1",
                "suite_sha256": hashlib.sha256(suite.read_bytes()).hexdigest(),
                "included_task_ids": ["a", "b"],
                "qualification_batches": [
                    {"id": "shopping", "task_ids": ["a", "b"]}
                ],
            }
        )
    )

    assert task_ids_from_scope(scope, suite, "shopping") == ("a", "b")
    suite.write_text('{"tasks": [1]}\n')
    with pytest.raises(ValueError, match="suite sha256"):
        task_ids_from_scope(scope, suite, "shopping")


def test_native_scope_rejects_unknown_and_excluded_batch_tasks(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text("{}")
    scope = tmp_path / "scope.json"
    payload = {
        "schema": "lex_browser_use_native_scope/1",
        "suite_sha256": hashlib.sha256(suite.read_bytes()).hexdigest(),
        "included_task_ids": ["a"],
        "qualification_batches": [{"id": "shopping", "task_ids": ["a", "excluded"]}],
    }
    scope.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="unknown or duplicate"):
        task_ids_from_scope(scope, suite, "missing")
    with pytest.raises(ValueError, match="contains excluded"):
        task_ids_from_scope(scope, suite, "shopping")


def test_native_scope_batch_shards_form_an_exact_stable_partition(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text("{}")
    task_ids = [f"task-{index}" for index in range(10)]
    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_native_scope/1",
                "suite_sha256": hashlib.sha256(suite.read_bytes()).hexdigest(),
                "included_task_ids": task_ids,
                "qualification_batches": [
                    {"id": "shopping", "shard_count": 3, "task_ids": task_ids}
                ],
            }
        )
    )

    shards = [
        task_ids_from_scope(scope, suite, "shopping", shard_count=3, shard_index=index)
        for index in range(3)
    ]

    assert shards == [
        ("task-0", "task-3", "task-6", "task-9"),
        ("task-1", "task-4", "task-7"),
        ("task-2", "task-5", "task-8"),
    ]
    assert sorted(item for shard in shards for item in shard) == sorted(task_ids)
    with pytest.raises(ValueError, match="provided together"):
        task_ids_from_scope(scope, suite, "shopping", shard_count=3)
    with pytest.raises(ValueError, match="smaller than shard count"):
        task_ids_from_scope(scope, suite, "shopping", shard_count=3, shard_index=3)
    with pytest.raises(ValueError, match="pre-registered value 3"):
        task_ids_from_scope(scope, suite, "shopping", shard_count=2, shard_index=0)
