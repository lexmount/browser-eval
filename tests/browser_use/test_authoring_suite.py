from __future__ import annotations

import pytest

from scripts.build_webarena_authoring_suite import build_authoring_suite


def _task(task_id: str, task_type: str, site: str) -> dict:
    return {
        "id": task_id,
        "tags": [task_type],
        "environment": {"sites": [site]},
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "provenance": {"kind": "official"},
            "actions": [{"action": "click", "selector": "#result"}],
        },
        "ground_truth": {
            "expected_actions": ["click"],
            "qualification": {"status": "pending"},
        },
    }


def test_authoring_suite_selects_only_pending_retrieve_repairs_for_site():
    suite = {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {},
        "tasks": [
            _task("webarena-1", "retrieve", "map"),
            _task("webarena-2", "mutate", "map"),
            _task("webarena-3", "retrieve", "gitlab"),
            _task("webarena-4", "retrieve", "map"),
        ],
    }
    qualification = {
        "tasks": [
            {"task_id": "webarena-1", "disposition": "needs_trace_repair"},
            {"task_id": "webarena-2", "disposition": "needs_trace_repair"},
            {"task_id": "webarena-3", "disposition": "needs_trace_repair"},
            {"task_id": "webarena-4", "disposition": "eligible"},
        ]
    }

    result = build_authoring_suite(suite, qualification, site="map")

    assert [task["id"] for task in result["tasks"]] == ["webarena-1"]
    task = result["tasks"][0]
    assert task["trajectory"]["actions"][0]["timeout_ms"] == 5_000
    assert task["trajectory"]["actions"][-1]["save_as"] == "authoring_page"
    assert task["ground_truth"]["qualification"]["status"] == "pending"
    assert result["benchmark"]["trajectory_authoring"] == {
        "non_canonical": True,
        "diagnostic_only": True,
        "source_disposition": "needs_trace_repair",
        "task_type": "retrieve",
        "site": "map",
        "task_count": 1,
        "diagnostic_target_timeout_ms": 5_000,
        "qualification_evidence": False,
    }


def test_authoring_suite_rejects_empty_scope():
    with pytest.raises(ValueError, match="no retrieve-only"):
        build_authoring_suite(
            {"tasks": [_task("webarena-1", "mutate", "reddit")]},
            {
                "tasks": [
                    {"task_id": "webarena-1", "disposition": "needs_trace_repair"}
                ]
            },
        )


def test_authoring_suite_can_resume_an_exact_subset():
    suite = {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {},
        "tasks": [
            _task("webarena-1", "retrieve", "shopping"),
            _task("webarena-2", "retrieve", "shopping"),
        ],
    }
    qualification = {
        "tasks": [
            {"task_id": "webarena-1", "disposition": "needs_trace_repair"},
            {"task_id": "webarena-2", "disposition": "needs_trace_repair"},
        ]
    }

    result = build_authoring_suite(
        suite, qualification, site="shopping", task_ids={"webarena-2"}
    )

    assert [task["id"] for task in result["tasks"]] == ["webarena-2"]
