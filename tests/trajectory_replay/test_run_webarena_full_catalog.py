from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_replay import _json_sha256
from scripts.run_webarena_full_catalog import _load_inputs


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
    loaded, _sites, tasks = _load_inputs(catalog_path, suite_path, sites_path)
    assert len(loaded["rows"]) == 812
    assert set(tasks) == {0}

    catalog["rows"][811]["recorded_response"] = {"status": "SUCCESS"}
    _write(catalog_path, catalog)
    with pytest.raises(ValueError, match="recorded answers"):
        _load_inputs(catalog_path, suite_path, sites_path)
