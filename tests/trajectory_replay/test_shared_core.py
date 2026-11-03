"""Both replay tracks share one core, and keep their own result and schema contracts."""

from __future__ import annotations

import json

import pytest

from harness.trajectory_replay.metrics import summarize_attempts as summarize_curated
from harness.trajectory_replay.public_webarena.metrics import (
    summarize_attempts as summarize_public,
)
from harness.trajectory_replay.public_webarena.suite import load_suite as load_public
from harness.trajectory_replay.suite import load_suite as load_curated


def _attempt(engine: str, attempt: int, status: str, duration: float, **fields) -> dict:
    return {
        "task_id": "task",
        "engine": engine,
        "attempt": attempt,
        "is_retry": attempt > 1,
        "status": status,
        "duration_ms": duration,
        **fields,
    }


def _suite_file(tmp_path, *, schema: str = "lex_browser_use_tasks/3", **task_fields):
    task = {
        "id": "task",
        "title": "Task",
        "difficulty": "simple",
        "tool_calls": [{"action": "navigate", "url": "https://example.com/", "save_as": "url"}],
        "assertions": [{"output": "url", "equals": "https://example.com/"}],
        "ground_truth": {
            "oracle": "deterministic_exact",
            "expected_actions": ["navigate"],
            "expected_outputs": {"url": "https://example.com/"},
        },
        "source": {"url": "https://example.com/"},
        **task_fields,
    }
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"schema": schema, "tasks": [task]}), encoding="utf-8")
    return path


def test_tracks_keep_their_own_logical_result_when_a_retry_fails_after_a_pass():
    rows = [_attempt("a", 1, "pass", 100.0), _attempt("a", 2, "fail", 900.0)]

    curated = summarize_curated(rows, engines=("a",))["functional"]["a"]
    public = summarize_public(rows, engines=("a",))["functional"]["a"]

    assert (curated["final_passes"], curated["duration_ms"]["n"]) == (1, 1)
    assert curated["duration_ms"]["mean"] == 100.0
    assert (public["final_passes"], public["duration_ms"]["n"]) == (0, 0)


def test_tracks_keep_their_own_latency_population_and_published_fields():
    rows = [
        _attempt("a", 1, "pass", 100.0, execution_duration_ms=10.0, network_har={"path": "x"}),
        _attempt("b", 1, "pass", 300.0, execution_duration_ms=20.0),
        _attempt("c", 1, "pass", 500.0, execution_duration_ms=40.0),
    ]
    engines = ("a", "b", "c")

    curated = summarize_curated(rows, engines=engines)
    public = summarize_public(rows, engines=engines)

    curated_resources = curated["resource_comparison"]
    public_resources = public["resource_comparison"]
    assert curated_resources["by_engine"]["a"]["duration_ms"]["mean"] == 100.0
    assert public_resources["by_engine"]["a"]["duration_ms"]["mean"] == 10.0
    assert "relative_ratios" not in curated_resources
    assert "intersection_results" not in curated_resources
    assert public_resources["intersection_results"] == 1
    ratios = public_resources["relative_ratios"]["by_engine"]
    assert ratios["c"]["duration_ms"]["mean"] == 4.0
    curated_attempt = curated["by_task"]["task"]["attempts"]["a"][0]
    public_attempt = public["by_task"]["task"]["attempts"]["a"][0]
    assert "execution_duration_ms" not in curated_attempt
    assert "network_har" not in curated_attempt
    assert public_attempt["execution_duration_ms"] == 10.0
    assert public_attempt["network_har"] == {"path": "x"}


def test_both_tracks_share_the_common_task_rules(tmp_path):
    suite_path = _suite_file(tmp_path, difficulty="impossible")

    for load in (load_curated, load_public):
        with pytest.raises(ValueError, match="task: difficulty must be simple, medium or complex"):
            load(suite_path)


def test_tracks_keep_their_own_schema_and_task_contract(tmp_path):
    legacy = _suite_file(tmp_path)

    curated_task = load_curated(legacy).tasks[0]
    public_task = load_public(legacy).tasks[0]

    assert type(curated_task) is type(public_task)
    assert curated_task.evaluator is None
    assert public_task.evaluator["kind"] == "exact_outputs"
    assert public_task.environment == {"kind": "public_web"}

    newer = _suite_file(tmp_path, schema="lex_browser_use_tasks/4")
    with pytest.raises(ValueError, match="task suite schema must be"):
        load_curated(newer)
    with pytest.raises(ValueError, match="trajectory schema must be"):
        load_public(newer)
