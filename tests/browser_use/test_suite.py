from __future__ import annotations

import json

import pytest

from harness.browser_use.suite import load_suite


def test_load_suite_assigns_non_overlapping_complexity_tiers(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_tasks/1",
                "tasks": [
                    {
                        "id": "simple",
                        "title": "Simple",
                        "steps": [
                            {"action": "navigate", "url": "https://example.com"},
                            {"action": "extract", "selector": "h1", "save_as": "heading"},
                        ],
                        "assertions": [{"output": "heading", "contains": "Example"}],
                    },
                    {
                        "id": "medium",
                        "title": "Medium",
                        "steps": [
                            {"action": "navigate", "url": "https://example.com"} for _ in range(6)
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
                "schema": "lex_browser_use_tasks/1",
                "tasks": [
                    {
                        "id": "bad",
                        "title": "Bad",
                        "steps": [{"action": "shell", "command": "true"}],
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
        "steps": [{"action": "navigate", "url": "https://example.com"}],
        "assertions": [],
    }
    path.write_text(
        json.dumps({"schema": "lex_browser_use_tasks/1", "tasks": [task, task]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate task id"):
        load_suite(path)


def test_initial_public_suite_has_ten_tasks_across_all_complexity_tiers():
    suite = load_suite("examples/browser_use/tasks.v1.json")

    assert len(suite.tasks) == 10
    assert {task.complexity for task in suite.tasks} == {
        "step_01_05",
        "step_06_10",
        "step_11_15",
        "step_16_20",
    }
