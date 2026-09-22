from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.qualification import task_contract_sha256
from harness.trajectory_replay.public_webarena.suite import BrowserTask, TaskSuite
from scripts.run_webarena_qualification import (
    _write_immutable,
    build_scoped_repair_queue,
    qualification_shards,
    qualification_waves,
    validate_completed_shard,
)


def _write_json(path, payload):
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_canonical_qualification_plan_is_an_exact_eighteen_shard_partition():
    shards = qualification_shards(
        Path("evaluation/browser-use-public/webarena-amd64-scope.json"),
        Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )

    assert len(shards) == 18
    assert sum(len(shard["task_ids"]) for shard in shards) == 172
    assert max(len(shard["task_ids"]) for shard in shards) <= 12


def test_parallel_qualification_waves_never_share_a_site():
    scope = json.loads(Path("evaluation/browser-use-public/webarena-amd64-scope.json").read_text())
    shards = qualification_shards(
        Path("evaluation/browser-use-public/webarena-amd64-scope.json"),
        Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )

    waves = qualification_waves(shards, scope, max_workers=3)
    sites = {row["id"]: set(row["sites"]) for row in scope["qualification_batches"]}

    assert len(waves) > 1
    assert all(len(wave) <= 3 for wave in waves)
    assert {shard["id"] for wave in waves for batch in wave for shard in batch} == {
        shard["id"] for shard in shards
    }
    for wave in waves:
        occupied = set()
        for batch in wave:
            batch_sites = sites[batch[0]["batch_id"]]
            assert occupied.isdisjoint(batch_sites)
            occupied.update(batch_sites)


def test_parallel_qualification_rejects_non_positive_worker_count():
    with pytest.raises(ValueError, match="positive"):
        qualification_waves((), {"qualification_batches": []}, max_workers=0)


def test_independent_slices_allow_overlapping_sites_in_the_same_wave():
    shards = (
        {"id": "a-0", "batch_id": "a"},
        {"id": "b-0", "batch_id": "b"},
    )
    scope = {
        "qualification_batches": [
            {"id": "a", "sites": ["reddit"]},
            {"id": "b", "sites": ["reddit"]},
        ]
    }

    shared = qualification_waves(shards, scope, max_workers=2)
    isolated = qualification_waves(shards, scope, max_workers=2, isolated_instances=2)

    assert len(shared) == 2
    assert len(isolated) == 1
    assert [batch[0]["id"] for batch in isolated[0]] == ["a-0", "b-0"]


def test_completed_shard_validation_binds_manifest_hashes_scope_and_attempts(tmp_path):
    output = tmp_path / "shopping-0-of-1"
    output.mkdir()
    shard = {
        "id": "shopping-0-of-1",
        "batch_id": "shopping",
        "shard_count": 1,
        "shard_index": 0,
        "task_ids": ("a", "b"),
    }
    attempts = [
        {
            "task_id": task_id,
            "engine": "chromium",
            "qualification_run": True,
            "qualification_pass_index": 1,
            "qualification_required_passes": 2,
            "is_retry": False,
            "attempt": 1,
        }
        for task_id in shard["task_ids"]
    ]
    for row in attempts:
        evidence = output / "tasks" / row["task_id"] / "chromium" / "attempt-1"
        evidence.mkdir(parents=True)
        row["evidence_directory"] = str(evidence)
        _write_json(
            evidence / "network.har",
            {
                "log": {
                    "creator": {"name": "browser-eval-cdp", "version": "3"},
                    "entries": [],
                }
            },
        )
    attempts_path = output / "attempts.jsonl"
    attempts_path.write_text("".join(json.dumps(row) + "\n" for row in attempts))
    _write_json(output / "summary.json", {"ok": True})
    suite_sha256 = "a" * 64
    scope_sha256 = "b" * 64
    _write_json(
        output / "manifest.json",
        {
            "suite_sha256": suite_sha256,
            "artifact_provenance": {
                "attempts_sha256": hashlib.sha256(attempts_path.read_bytes()).hexdigest(),
                "summary_sha256": hashlib.sha256(
                    (output / "summary.json").read_bytes()
                ).hexdigest(),
            },
            "protocol": {
                "qualification_run": True,
                "qualification_pass_index": 1,
                "qualification_required_passes": 2,
                "engines": ["chromium"],
                "max_failure_retries": 0,
                "task_filter": ["a", "b"],
                "task_selection": {
                    "kind": "native_scope_batch",
                    "scope_sha256": scope_sha256,
                    "batch_id": "shopping",
                    "shard_count": 1,
                    "shard_index": 0,
                    "qualification_pass_index": 1,
                    "qualification_required_passes": 2,
                },
            },
        },
    )

    validate_completed_shard(
        output,
        suite_sha256=suite_sha256,
        scope_sha256=scope_sha256,
        shard=shard,
        qualification_pass_index=1,
    )

    attempts[0]["is_retry"] = True
    attempts_path.write_text("".join(json.dumps(row) + "\n" for row in attempts))
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["artifact_provenance"]["attempts_sha256"] = hashlib.sha256(
        attempts_path.read_bytes()
    ).hexdigest()
    _write_json(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="primary-run policy"):
        validate_completed_shard(
            output,
            suite_sha256=suite_sha256,
            scope_sha256=scope_sha256,
            shard=shard,
            qualification_pass_index=1,
        )


def test_completed_shard_validation_rejects_old_har_creator(tmp_path):
    output = tmp_path / "shopping-0-of-1"
    evidence = output / "tasks" / "a" / "chromium" / "attempt-1"
    evidence.mkdir(parents=True)
    attempts_path = output / "attempts.jsonl"
    attempts_path.write_text(
        json.dumps(
            {
                "task_id": "a",
                "engine": "chromium",
                "qualification_run": True,
                "qualification_pass_index": 1,
                "qualification_required_passes": 2,
                "is_retry": False,
                "attempt": 1,
                "evidence_directory": str(evidence),
            }
        )
        + "\n"
    )
    _write_json(output / "summary.json", {"ok": True})
    _write_json(
        evidence / "network.har",
        {"log": {"creator": {"name": "browser-eval-cdp", "version": "1"}}},
    )
    _write_json(
        output / "manifest.json",
        {
            "suite_sha256": "a" * 64,
            "artifact_provenance": {
                "attempts_sha256": hashlib.sha256(attempts_path.read_bytes()).hexdigest(),
                "summary_sha256": hashlib.sha256(
                    (output / "summary.json").read_bytes()
                ).hexdigest(),
            },
            "protocol": {
                "qualification_run": True,
                "qualification_pass_index": 1,
                "qualification_required_passes": 2,
                "engines": ["chromium"],
                "max_failure_retries": 0,
                "task_filter": ["a"],
                "task_selection": {
                    "kind": "native_scope_batch",
                    "scope_sha256": "b" * 64,
                    "batch_id": "shopping",
                    "shard_count": 1,
                    "shard_index": 0,
                    "qualification_pass_index": 1,
                    "qualification_required_passes": 2,
                },
            },
        },
    )

    with pytest.raises(ValueError, match="HAR creator"):
        validate_completed_shard(
            output,
            suite_sha256="a" * 64,
            scope_sha256="b" * 64,
            shard={
                "id": "shopping-0-of-1",
                "batch_id": "shopping",
                "shard_count": 1,
                "shard_index": 0,
                "task_ids": ("a",),
            },
            qualification_pass_index=1,
        )


def test_immutable_queue_writer_never_overwrites_different_evidence(tmp_path):
    path = tmp_path / "queue.json"
    _write_immutable(path, {"value": 1})
    _write_immutable(path, {"value": 1})

    with pytest.raises(ValueError, match="refusing to overwrite"):
        _write_immutable(path, {"value": 2})


def test_scoped_queue_covers_measured_and_architecture_excluded_tasks(tmp_path):
    actions = ({"action": "click", "selector": "#save"},)
    included = BrowserTask(
        "included",
        "Included",
        actions,
        (),
        (),
        "simple",
        {"expected_actions": ["click"]},
        {"url": "https://example.test/included"},
        {"kind": "webarena_verified", "task_id": 1, "revision": 1},
        {"kind": "webarena_verified"},
        {"schema": "lex_browser_trajectory/1", "actions": list(actions)},
    )
    excluded = BrowserTask(
        "excluded",
        "Excluded",
        actions,
        (),
        (),
        "simple",
        {"expected_actions": ["click"]},
        {"url": "https://example.test/excluded"},
        {"kind": "webarena_verified", "task_id": 2, "revision": 1},
        {"kind": "webarena_verified"},
        {"schema": "lex_browser_trajectory/1", "actions": list(actions)},
    )
    suite = TaskSuite("lex_browser_use_tasks/4", (included, excluded))
    attempts = tmp_path / "attempts.jsonl"
    rows = []
    for pass_index in (1, 2):
        evidence = tmp_path / f"included-pass-{pass_index}"
        evidence.mkdir()
        rows.append(
            {
                "task_id": "included",
                "engine": "chromium",
                "attempt": 1,
                "is_retry": False,
                "qualification_run": True,
                "qualification_pass_index": pass_index,
                "qualification_required_passes": 2,
                "run_id": f"qualification-pass-{pass_index}",
                "combined_status": "fail",
                "process_status": "fail",
                "result_status": "fail",
                "trace": [],
                "evidence_directory": str(evidence),
                "task_contract_sha256": task_contract_sha256(
                    {
                        "id": included.task_id,
                        "title": included.title,
                        "trajectory": included.trajectory,
                        "evaluator": included.evaluator,
                        "environment": included.environment,
                        "ground_truth": included.ground_truth,
                        "source": included.source,
                    }
                ),
            }
        )
    attempts.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    scope = {
        "architecture": "amd64",
        "included_task_ids": ["included"],
        "excluded_tasks": [
            {
                "task_id": "excluded",
                "sites": ["map", "wikipedia"],
                "incompatible_sites": ["wikipedia"],
                "reason": "Wikipedia is not native amd64",
            }
        ],
    }

    queue = build_scoped_repair_queue(suite, [attempts], scope, scope_sha256="f" * 64)

    assert [row["task_id"] for row in queue["tasks"]] == ["included", "excluded"]
    assert queue["tasks"][1]["adjudication"] == {
        "status": "skipped",
        "reason": "Wikipedia is not native amd64",
        "scope_exclusion": {
            "architecture": "amd64",
            "sites": ["map", "wikipedia"],
            "incompatible_sites": ["wikipedia"],
        },
    }
    assert queue["native_scope"]["included_count"] == 1
    assert queue["native_scope"]["excluded_count"] == 1
