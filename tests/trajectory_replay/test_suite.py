from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from harness.trajectory_replay.report_inputs import replay_input_path
from harness.trajectory_replay.suite import load_suite, validate_ground_truth_evidence


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
                "tasks": [_task("bad", [{"action": "shell", "command": "true"}])],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported action"):
        load_suite(path)


@pytest.mark.parametrize("action", ["click", "fill", "select"])
@pytest.mark.parametrize("selector", [None, "", "  ", 12])
def test_load_suite_rejects_ref_only_and_invalid_selectors(tmp_path, action, selector):
    path = tmp_path / "tasks.json"
    calls = [
        {"action": action, "ref": 1, "selector": selector},
        {"action": "get_text", "selector": "h1", "save_as": "heading"},
    ]
    path.write_text(
        json.dumps({"schema": "lex_browser_use_tasks/3", "tasks": [_task("bad", calls)]})
    )
    with pytest.raises(ValueError, match="bad: call 1: .*ref is not supported"):
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


def test_suite_has_thirty_ground_truth_tasks_with_requested_difficulty_mix():
    suite = load_suite(replay_input_path(current="curated"))

    assert len(suite.tasks) == 30
    assert len({task.task_id for task in suite.tasks}) == 30
    assert all(call["action"] != "wait" for task in suite.tasks for call in task.tool_calls)
    counts = {
        difficulty: sum(task.complexity == difficulty for task in suite.tasks)
        for difficulty in ("simple", "medium", "complex")
    }
    assert counts == {"simple": 5, "medium": 10, "complex": 15}
    assert all(task.ground_truth["oracle"] == "deterministic_exact" for task in suite.tasks)
    navigations = [
        str(call["url"])
        for task in suite.tasks
        for call in task.tool_calls
        if call["action"] == "navigate"
    ]
    assert navigations
    assert all(urlparse(url).scheme == "https" for url in navigations)
    assert all(
        urlparse(url).hostname not in {"localhost", "127.0.0.1", "host.docker.internal"}
        for url in navigations
    )
    assert all("{fixture_origin}" not in url for url in navigations)
    assert len({urlparse(url).hostname for url in navigations}) >= 8


def test_version_language_path_targets_desktop_controls_and_waits_for_navigation():
    suite = load_suite(replay_input_path(current="curated"))
    task = next(t for t in suite.tasks if t.task_id == "python-version-language")
    selects = [call for call in task.tool_calls if call["action"] == "select"]
    assert len(selects) == 3
    assert all(call["selector"].startswith(".switchers ") for call in selects)
    assert all(call.get("wait_for_navigation") is True for call in selects)
    assert all(call.get("wait_until") == "domcontentloaded" for call in selects)


def test_suite_uses_public_targets_and_repository_derived_capability_coverage():
    suite = load_suite(replay_input_path(current="curated"))
    assert all(task.source.get("kind") == "public_web" for task in suite.tasks)
    assert all(str(task.source.get("url") or "").startswith("https://") for task in suite.tasks)
    basis_urls = [str(url) for task in suite.tasks for url in task.source.get("design_basis") or []]
    assert any("Lexbench-Headless-Browser" in url for url in basis_urls)
    assert any("lexmount/moli" in url for url in basis_urls)
    actions = {str(call["action"]) for task in suite.tasks for call in task.tool_calls}
    assert {
        "click",
        "fill",
        "select",
        "back",
        "forward",
        "get_property",
        "get_attribute",
    } <= actions


def test_codex_browser_ground_truth_evidence_matches_dataset_exactly():
    dataset_path = replay_input_path(current="curated")
    evidence = json.loads(
        Path("examples/trajectory_replay/ground_truth_validation.json").read_text(encoding="utf-8")
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


def test_ground_truth_validation_rejects_evidence_for_another_suite(tmp_path):
    dataset_path = replay_input_path(current="curated")
    suite = load_suite(dataset_path)
    evidence = json.loads(
        Path("examples/trajectory_replay/ground_truth_validation.json").read_text(encoding="utf-8")
    )
    evidence["dataset_sha256"] = "0" * 64
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="suite sha256"):
        validate_ground_truth_evidence(dataset_path, suite, evidence_path)
