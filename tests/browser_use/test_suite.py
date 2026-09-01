from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from harness.browser_use.suite import load_suite


def _task(task_id, calls, expected="Example"):
    return {
        "id": task_id,
        "title": task_id.title(),
        "difficulty": "simple",
        "tool_calls": calls,
        "assertions": [{"output": "heading", "equals": expected}],
        "ground_truth": {
            "oracle": "deterministic_exact",
            "expected_outputs": {"heading": expected},
            "expected_actions": [call["action"] for call in calls],
        },
        "source": {"url": "https://example.com/source"},
    }


def test_load_suite_uses_declared_difficulty_and_ground_truth(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_tasks/3",
                "tasks": [
                    _task(
                        "simple",
                        [
                            {"action": "navigate", "url": "https://example.com", "save_as": "page"},
                            {"action": "get_text", "selector": "h1", "save_as": "heading"},
                        ],
                    )
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_suite(path)

    assert suite.tasks[0].complexity == "simple"
    assert suite.tasks[0].ground_truth["expected_outputs"]["heading"] == "Example"


def test_load_suite_rejects_unsupported_actions(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_tasks/3",
                "tasks": [
                    _task("bad", [{"action": "shell", "command": "true"}])
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported action"):
        load_suite(path)


def test_load_suite_rejects_duplicate_task_ids(tmp_path):
    path = tmp_path / "tasks.json"
    task = _task(
        "duplicate",
        [
            {"action": "navigate", "url": "https://example.com", "save_as": "page"},
            {"action": "get_text", "selector": "h1", "save_as": "heading"},
        ],
    )
    path.write_text(
        json.dumps({"schema": "lex_browser_use_tasks/3", "tasks": [task, task]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate task id"):
        load_suite(path)


def test_suite_has_thirty_ground_truth_tasks_balanced_by_difficulty():
    suite = load_suite("examples/browser_use/tasks.v4.json")

    assert len(suite.tasks) == 30
    assert len({task.task_id for task in suite.tasks}) == 30
    assert all(call["action"] != "wait" for task in suite.tasks for call in task.tool_calls)
    counts = {
        difficulty: sum(task.complexity == difficulty for task in suite.tasks)
        for difficulty in ("simple", "medium", "complex")
    }
    assert counts == {"simple": 10, "medium": 10, "complex": 10}
    assert all(task.ground_truth["oracle"] == "deterministic_exact" for task in suite.tasks)

    probes = suite.tasks[15:]
    scenarios = [
        call["url"].split("scenario=", 1)[1]
        for task in probes
        for call in task.tool_calls
        if call["action"] == "navigate"
    ]
    assert len(scenarios) == 15
    assert len(set(scenarios)) == 15
    assert all(task.step_count >= 4 for task in probes if task.complexity == "medium")
    assert all(task.step_count >= 5 for task in probes if task.complexity == "complex")


def test_suite_preserves_existing_cases_and_adds_repository_derived_probes():
    suite = load_suite("examples/browser_use/tasks.v4.json")
    urls = [str(task.source.get("url") or "") for task in suite.tasks]
    assert any(url.startswith("https://github.com/lexmount/browser-harness-eval") for url in urls)
    assert any(url.startswith("https://github.com/lexmount/Lexbench-Headless-Browser") for url in urls)
    assert any(url.startswith("https://github.com/lexmount/moli") for url in urls)


def test_codex_browser_ground_truth_evidence_matches_dataset_exactly():
    dataset_path = Path("examples/browser_use/tasks.v4.json")
    evidence = json.loads(
        Path("examples/browser_use/ground_truth_validation.json").read_text(encoding="utf-8")
    )
    assert evidence["dataset_sha256"] == hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    assert evidence["validator"] == "Codex Desktop in-app Browser Tool"
    assert evidence["automatic_browser_replay"] == {
        "passed": 30,
        "total": 30,
        "status": "pass",
    }
    assert evidence["human_review"]["status"] == "pending"

    suite = load_suite(dataset_path)
    observed = {row["task_id"]: row for row in evidence["results"]}
    assert set(observed) == {task.task_id for task in suite.tasks}
    for task in suite.tasks:
        row = observed[task.task_id]
        assert row["status"] == "pass"
        assert row["observed"] == task.ground_truth["expected_outputs"]
