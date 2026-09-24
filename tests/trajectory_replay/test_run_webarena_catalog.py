from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.full_catalog import (
    TRUSTED_RECORDED_HOSTS,
    completed_task_ids,
    load_catalog_inputs,
    row_source_sites,
)
from harness.trajectory_replay.remote_runtime import rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256
from harness.trajectory_replay.catalog_runner import _recover_completed_attempts, _task_ids


def test_task_ids_preserve_requested_official_order():
    assert _task_ids([3, 7, 11, 20], 7, 20) == [7, 11, 20]
    assert _task_ids([3, 7, 11, 20], None, None) == [3, 7, 11, 20]


@pytest.mark.parametrize("bounds", [(-1, 1), (2, 1), (0, 812)])
def test_task_ids_reject_out_of_catalog_range(bounds):
    with pytest.raises(ValueError, match="catalog"):
        _task_ids([0, 1, 2], *bounds)


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_catalog_requires_one_hash_bound_row_in_official_order(tmp_path: Path) -> None:
    sites_path = tmp_path / "sites.json"
    sites_hash = _write(
        sites_path,
        {
            "schema": "lex_webarena_remote_sites/1",
            "sites": {
                "shopping": "https://shopping.example.test",
                "shopping_admin": "https://admin.example.test/admin",
                "reddit": "https://reddit.example.test",
                "gitlab": "https://gitlab.example.test",
            },
        },
    )
    action = {"action": "navigate", "url": "https://example.test", "save_as": "page"}
    suite_path = tmp_path / "portable.json"
    suite_hash = _write(
        suite_path,
        {
            "schema": "lex_browser_use_tasks/4",
            "tasks": [
                {
                    "id": "webarena-0",
                    "title": "Open page",
                    "difficulty": "simple",
                    "tags": ["webarena"],
                    "trajectory": {
                        "schema": "lex_browser_trajectory/1",
                        "actions": [action],
                    },
                    "assertions": [],
                    "evaluator": {
                        "kind": "webarena_verified",
                        "task_id": 0,
                        "revision": 2,
                    },
                    "environment": {"kind": "webarena_verified"},
                    "ground_truth": {
                        "oracle": "webarena_verified",
                        "expected_actions": ["navigate"],
                    },
                    "source": {"url": "https://example.test"},
                }
            ],
        },
    )
    rows = [
        {
            "task_id": task_id,
            "mode": "portable" if task_id == 0 else "direct",
            "bound_calls_sha256": _json_sha256([action]),
            **({} if task_id == 0 else {"source_actions": [action]}),
        }
        for task_id in range(812)
    ]
    catalog_path = tmp_path / "catalog.json"
    catalog = {
        "schema": "lex_webarena_full_replay_catalog/1",
        "task_ids": list(range(812)),
        "site_map_sha256": sites_hash,
        "portable_suite_sha256": suite_hash,
        "rows": rows,
    }
    _write(catalog_path, catalog)
    loaded, _sites, tasks = load_catalog_inputs(catalog_path, suite_path, sites_path)
    assert len(loaded["rows"]) == 812
    assert set(tasks) == {0}

    catalog["rows"][811]["recorded_response"] = {"status": "SUCCESS"}
    _write(catalog_path, catalog)
    with pytest.raises(ValueError, match="recorded answers"):
        load_catalog_inputs(catalog_path, suite_path, sites_path)


def test_rows_can_rebind_accepted_actions_from_distinct_source_deployments() -> None:
    from webarena_verified.types.task import WebArenaSite

    target = {WebArenaSite.SHOPPING: "https://new-shop.example.test"}
    actions = [{"action": "navigate", "url": "https://old-shop-a.example.test/product"}]
    row = {"source_site_urls": {"shopping": "https://old-shop-a.example.test"}}
    rebound = rebind_remote_calls(
        actions, target, set(TRUSTED_RECORDED_HOSTS), row_source_sites({}, row)
    )
    assert rebound[0]["url"] == "https://new-shop.example.test/product"

    actions_b = [{"action": "navigate", "url": "https://old-shop-b.example.test/product"}]
    row_b = {"source_site_urls": {"shopping": "https://old-shop-b.example.test"}}
    rebound_b = rebind_remote_calls(
        actions_b, target, set(TRUSTED_RECORDED_HOSTS), row_source_sites({}, row_b)
    )
    assert rebound_b == rebound


def test_progress_must_be_a_strict_execution_prefix(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    progress.write_text('{"task_id":0}\n{"task_id":2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="strict prefix"):
        completed_task_ids(progress, [0, 1, 2])


def test_resume_commits_finished_orphan_without_reexecuting(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    task = tmp_path / "task-0"
    task.mkdir()
    trace = task / "trace.json"
    trace.write_text("[]\n", encoding="utf-8")
    row = {
        "task_id": 0,
        "status": "fail",
        "action_count": 1,
        "evidence_sha256": {"trace": hashlib.sha256(trace.read_bytes()).hexdigest()},
    }
    (task / "result.json").write_text(json.dumps(row), encoding="utf-8")

    assert _recover_completed_attempts(tmp_path, progress, [0, 1]) == {0}
    assert json.loads(progress.read_text(encoding="utf-8"))["task_id"] == 0


def test_resume_rejects_partial_orphan_instead_of_rerunning(tmp_path: Path) -> None:
    (tmp_path / "task-0").mkdir()
    with pytest.raises(ValueError, match="interrupted evidence"):
        _recover_completed_attempts(tmp_path, tmp_path / "progress.jsonl", [0])
