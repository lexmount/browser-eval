from __future__ import annotations

import json

import pytest

from harness.browser_use.suite import load_suite


def test_load_suite_assigns_non_overlapping_complexity_tiers(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_tasks/2",
                "tasks": [
                    {
                        "id": "simple",
                        "title": "Simple",
                        "tool_calls": [
                            {"action": "navigate", "url": "https://example.com", "save_as": "page"},
                            {"action": "get_text", "selector": "h1", "save_as": "heading"},
                        ],
                        "assertions": [{"output": "heading", "contains": "Example"}],
                    },
                    {
                        "id": "medium",
                        "title": "Medium",
                        "tool_calls": [
                            {
                                "action": "navigate",
                                "url": "https://example.com",
                                "save_as": f"page{i}",
                            }
                            for i in range(6)
                        ],
                        "assertions": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_suite(path)

    assert suite.tasks[0].complexity == "step_01_05"
    assert suite.tasks[1].complexity == "step_06_10"


def test_load_suite_rejects_unsupported_actions(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_tasks/2",
                "tasks": [
                    {
                        "id": "bad",
                        "title": "Bad",
                        "tool_calls": [{"action": "shell", "command": "true"}],
                        "assertions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported action"):
        load_suite(path)


def test_load_suite_rejects_duplicate_task_ids(tmp_path):
    path = tmp_path / "tasks.json"
    task = {
        "id": "duplicate",
        "title": "Duplicate",
        "tool_calls": [{"action": "navigate", "url": "https://example.com", "save_as": "page"}],
        "assertions": [],
    }
    path.write_text(
        json.dumps({"schema": "lex_browser_use_tasks/2", "tasks": [task, task]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate task id"):
        load_suite(path)


def test_public_suite_has_ten_distinct_real_tasks_and_no_fake_wait_steps():
    suite = load_suite("examples/browser_use/tasks.v2.json")

    assert len(suite.tasks) == 10
    assert len({task.task_id for task in suite.tasks}) == 10
    assert all(call["action"] != "wait" for task in suite.tasks for call in task.tool_calls)
    primary_hosts = {task.tool_calls[0]["url"].split("/", 3)[2] for task in suite.tasks}
    assert len(primary_hosts) == 10
