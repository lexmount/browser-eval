from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.trajectory_replay.public_webarena.suite import BrowserTask
from harness.trajectory_replay.webarena_replay import _json_sha256
from scripts import run_webarena_full_chromium as sweep
from scripts.run_webarena_full_chromium import (
    _append,
    _attempt_pending_row,
    _completed,
    _template_task,
    _validate_template_candidate,
)


def test_resume_skips_every_recorded_case_in_official_order(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    _append(progress, {"task_id": 0, "status": "needs_trajectory"})
    _append(progress, {"task_id": 2, "status": "pass"})
    assert _completed(progress, [0, 1, 2, 3]) == {0, 2}
    _append(progress, {"task_id": 1, "status": "fail"})
    with pytest.raises(ValueError, match="official case order"):
        _completed(progress, [0, 1, 2, 3])


def test_template_task_keeps_source_immutable_and_binds_target_evaluator() -> None:
    source = BrowserTask(
        task_id="webarena-4",
        title="Visit books",
        tool_calls=({"action": "click"},),
        assertions=(),
        tags=("webarena",),
        difficulty="simple",
        ground_truth={
            "response_builder": {"kind": "null_after_verified_path", "task_type": "navigate"}
        },
        source={},
        evaluator={"kind": "webarena_verified", "task_id": 4, "revision": 2},
    )
    candidate = {
        "task_id": 0,
        "replacements": [],
        "actions": [{"action": "click", "target": {"by": "text", "value": "books"}}],
    }
    target = _template_task(source, candidate, SimpleNamespace(intent="Visit books", revision=3))
    assert target.task_id == "webarena-0"
    assert target.evaluator == {"kind": "webarena_verified", "task_id": 0, "revision": 3}
    assert source.task_id == "webarena-4"
    assert target.tool_calls[0]["target"]["value"] == "books"


def test_candidate_uses_shared_remote_execution(monkeypatch, tmp_path: Path) -> None:
    task = BrowserTask(
        task_id="webarena-2",
        title="Visit",
        tool_calls=({"action": "navigate", "url": "http://example.test"},),
        assertions=(),
        tags=("webarena",),
        difficulty="simple",
        ground_truth={},
        source={},
    )
    observed = {}

    def fake_run_task(**kwargs):
        observed.update(kwargs)
        return {"status": "unsupported", "task_id": 2}

    monkeypatch.setattr(sweep, "_run_task", fake_run_task)
    monkeypatch.setattr(sweep, "load_corrections", lambda: object())
    result = sweep._run_candidate(
        task,
        SimpleNamespace(),
        "http://localhost:9222",
        tmp_path,
        {"site": "http://site.example"},
    )
    assert result["status"] == "needs_response_builder"
    assert observed["portable_task"] is task
    assert observed["row"]["actions"] == list(task.tool_calls)


def test_template_candidate_checks_source_target_and_action_hashes() -> None:
    source = BrowserTask(
        task_id="webarena-4",
        title="Visit",
        tool_calls=({"action": "navigate", "url": "http://example.test"},),
        assertions=(),
        tags=("webarena",),
        difficulty="simple",
        ground_truth={},
        source={},
    )
    candidate = {
        "task_id": 5,
        "source_actions_sha256": _json_sha256(list(source.tool_calls)),
        "actions_sha256": _json_sha256(list(source.tool_calls)),
        "actions": list(source.tool_calls),
        "intent_template_id": 42,
    }
    official = SimpleNamespace(intent_template_id=42)
    _validate_template_candidate(candidate, source, official)
    candidate["actions"] = [{"action": "navigate", "url": "http://wrong.test"}]
    with pytest.raises(ValueError, match="candidate changed"):
        _validate_template_candidate(candidate, source, official)


def test_curated_transfer_uses_shared_browser_tool_path(monkeypatch, tmp_path: Path) -> None:
    observed = {}

    def fake_run_task(**kwargs):
        observed.update(kwargs)
        return {"task_id": 2, "status": "pass"}

    monkeypatch.setattr(sweep, "_run_task", fake_run_task)
    monkeypatch.setattr(sweep, "load_corrections", lambda: object())
    benchmark = SimpleNamespace(get_task=lambda _task_id: SimpleNamespace(is_retrieve_task=False))
    candidate = {
        "task_id": 2,
        "actions": [{"action": "click", "selector": "a[href='/f/books']"}],
    }
    outcome = sweep._run_curated_candidate(
        candidate, benchmark, "http://localhost:9222", tmp_path, {"reddit": "http://site.test"}
    )
    assert outcome["status"] == "pass"
    assert observed["row"]["actions"] == candidate["actions"]
    assert "portable_task" not in observed


def test_one_candidate_setup_error_is_recorded_without_stopping_later_cases(
    monkeypatch, tmp_path: Path
) -> None:
    def broken_candidate(*_args, **_kwargs):
        raise RuntimeError("recorded target is malformed")

    monkeypatch.setattr(sweep, "_run_candidate", broken_candidate)
    common = {
        "template_curated": {},
        "template_tasks": {},
        "authored_by_id": {},
        "by_id": {1: object(), 2: object()},
        "benchmark": object(),
        "endpoint": "http://localhost:9222",
        "output": tmp_path,
        "sites": {},
        "action_timeout_ms": 15_000,
    }
    first = _attempt_pending_row({"task_id": 1, "source": "recorded"}, **common)
    second = _attempt_pending_row({"task_id": 2, "source": "recorded"}, **common)
    assert [first["task_id"], second["task_id"]] == [1, 2]
    assert first["status"] == second["status"] == "candidate_setup_failure"
    assert "recorded target is malformed" in second["error"]
