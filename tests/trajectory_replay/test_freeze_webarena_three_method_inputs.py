import json
from pathlib import Path

import pytest

from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from scripts.freeze_webarena_three_method_inputs import _digest, _sha256, freeze


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_freeze_requires_all_90_tasks_and_current_retrieval_contracts(tmp_path: Path) -> None:
    site_map = tmp_path / "sites.json"
    _write(site_map, {
        "schema": "lex_webarena_remote_sites/1",
        "sites": {
            "reddit": "https://reddit.example.test",
            "gitlab": "https://gitlab.example.test",
            "shopping": "https://shopping.example.test",
            "shopping_admin": "https://admin.example.test/admin",
        },
    })
    sites = load_remote_sites(site_map)
    calls = [{"action": "navigate", "url": "http://127.0.0.1:9999/f/DIY"}]
    bound = rebind_remote_calls(calls, sites, {"127.0.0.1"})
    dataset = tmp_path / "dataset.json"
    selection = tmp_path / "selection.json"
    _write(selection, {
        "schema": "lex_webarena_verified_pilot/1",
        "tasks": [{"task_id": task_id} for task_id in reversed(range(90))],
    })
    tasks = [
        {
            "task_id": task_id,
            "tool_calls": calls,
            "task": {"intent": f"Task {task_id}"},
            "evaluation_input": {"task_type": "MUTATE"},
        }
        for task_id in range(90)
    ]
    _write(dataset, {"schema": "lex_webarena_verified_trajectory_dataset/1", "trajectories": tasks})
    affected = tmp_path / "affected.json"
    _write(affected, {
        "schema": "original36_affected_cohort_inputs/1",
        "promotable": True,
        "site_map_sha256": _sha256(site_map),
        "candidate_task_ids": list(range(36)),
        "candidates": [
            {
                "task_id": task_id,
                "actions": bound,
                "bound_calls_sha256": _digest(bound),
                "source_calls_sha256": _digest(calls),
                "readiness": [],
            }
            for task_id in range(36)
        ],
    })
    binary = tmp_path / "browser"
    binary.write_bytes(b"fixed-browser")
    kwargs = dict(
        dataset_path=dataset,
        selection_path=selection,
        affected36_path=affected,
        site_map_path=site_map,
        binary_path=binary,
        method_id="moli-fixed",
        engine="moli",
    )
    result = freeze(**kwargs)
    assert len(result["tasks"]) == 90
    assert result["task_ids"] == list(range(90))
    assert result["tasks"][0]["actions"] == result["tasks"][36]["actions"]
    assert result["browser_binary_sha256"] == _sha256(binary)
    tasks[36]["evaluation_input"]["task_type"] = "RETRIEVE"
    _write(dataset, {"schema": "lex_webarena_verified_trajectory_dataset/1", "trajectories": tasks})
    with pytest.raises(ValueError, match=r"missing: \[36\]"):
        freeze(**kwargs)
