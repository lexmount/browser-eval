from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from harness.trajectory_replay import catalog_runner
from harness.trajectory_replay.catalog_binding import rebind_catalog
from harness.trajectory_replay.catalog_contracts import (
    TRUSTED_RECORDED_HOSTS,
    apply_action_overrides,
    catalog_action_overrides,
    completed_task_ids,
    file_sha256,
    load_action_overrides,
    load_catalog_inputs,
    row_source_sites,
)
from harness.trajectory_replay.catalog_runner import (
    _load_task_selection,
    _recover_completed_attempts,
    _run_with_setup_recovery,
    _task_ids,
    _task_origins,
)
from harness.trajectory_replay.remote_execution import finalize_timed_out_task
from harness.trajectory_replay.remote_runtime import rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256


def test_task_ids_preserve_requested_official_order():
    assert _task_ids([3, 7, 11, 20], 7, 20) == [7, 11, 20]
    assert _task_ids([3, 7, 11, 20], None, None) == [3, 7, 11, 20]
    assert _task_ids([3, 7, 11, 20], None, None, [7, 20]) == [7, 20]


def test_external_timeout_commits_the_active_action_and_hashes(tmp_path: Path) -> None:
    task_dir = tmp_path / "task-7"
    task_dir.mkdir()
    (task_dir / ".trace.partial.json").write_text(
        json.dumps(
            [
                {
                    "index": 1,
                    "action": "navigate",
                    "arguments": {"action": "navigate", "url": "http://example.test/start"},
                    "status": "pass",
                    "duration_ms": 10,
                    "output": "ok",
                },
                {
                    "index": 2,
                    "action": "navigate",
                    "arguments": {"action": "navigate", "url": "http://example.test"},
                    "status": "running",
                    "duration_ms": None,
                    "output": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    row = {
        "task_id": 7,
        "actions": [{"action": "navigate", "url": "http://example.test"}],
        "omitted_diagnostic_observer_sha256": None,
    }

    result = finalize_timed_out_task(
        task_dir, row, duration_ms=360_000, task_timeout_ms=300_000
    )

    trace = json.loads((task_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace[-1]["status"] == "fail"
    assert "action 2 (navigate)" in trace[-1]["error"]
    assert result["failure_stage"] == "browser_tool_execution"
    assert result["completed_actions"] == 1
    assert result["runtime_poisoned"] is True
    assert not (task_dir / ".trace.partial.json").exists()
    for kind, digest in result["evidence_sha256"].items():
        filename = {
            "trace": "trace.json",
            "readiness": "readiness.json",
            "outputs": "outputs.json",
        }[kind]
        assert file_sha256(task_dir / filename) == digest


def test_external_timeout_counts_source_success_actions(tmp_path: Path) -> None:
    task_dir = tmp_path / "task-8"
    task_dir.mkdir()
    (task_dir / ".trace.partial.json").write_text(
        json.dumps(
            [
                {
                    "index": 1,
                    "action": "navigate",
                    "arguments": {},
                    "status": "success",
                    "duration_ms": 10,
                    "output": "ok",
                },
                {
                    "index": 2,
                    "action": "evaluate",
                    "arguments": {},
                    "status": "running",
                    "duration_ms": None,
                    "output": "",
                },
            ]
        ),
        encoding="utf-8",
    )

    result = finalize_timed_out_task(
        task_dir,
        {"task_id": 8, "actions": [{}, {}]},
        duration_ms=300_000,
        task_timeout_ms=300_000,
    )

    assert result["completed_actions"] == 1


@pytest.mark.parametrize("bounds", [(-1, 1), (2, 1), (0, 812)])
def test_task_ids_reject_out_of_catalog_range(bounds):
    with pytest.raises(ValueError, match="catalog"):
        _task_ids([0, 1, 2], *bounds)


@pytest.mark.parametrize("selected", [[2, 1], [1, 1], [1, 4]])
def test_task_ids_reject_invalid_explicit_repairs(selected):
    with pytest.raises(ValueError, match="task IDs"):
        _task_ids([0, 1, 2], None, None, selected)


def test_task_ids_reject_mixed_range_and_explicit_selection():
    with pytest.raises(ValueError, match="cannot be combined"):
        _task_ids([0, 1, 2], 0, 2, [1])


def test_task_origins_excludes_unrelated_deployments() -> None:
    from webarena_verified.types.task import WebArenaSite

    task = type(
        "Task",
        (),
        {"sites": (WebArenaSite.GITLAB, WebArenaSite.WIKIPEDIA)},
    )()
    benchmark = type(
        "Benchmark",
        (),
        {"get_task": lambda self, task_id: task},
    )()
    sites = {
        WebArenaSite.GITLAB: "https://gitlab.example.test/root/path",
        WebArenaSite.WIKIPEDIA: "http://wikipedia.example.test",
        WebArenaSite.MAP: "http://unavailable-map.example.test",
    }

    assert _task_origins(benchmark, 17, sites) == {
        "https://gitlab.example.test",
        "http://wikipedia.example.test",
    }


def test_task_selection_is_schema_bound(tmp_path: Path):
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps({"schema": "lex_webarena_replay_selection/1", "task_ids": [1, 7]}),
        encoding="utf-8",
    )
    assert _load_task_selection(selection) == [1, 7]

    selection.write_text(json.dumps({"task_ids": [1, 7]}), encoding="utf-8")
    with pytest.raises(ValueError, match="contract"):
        _load_task_selection(selection)


class _FakeBrowserProcess:
    def __init__(self, returncode: int | None = None):
        self.returncode = returncode
        self.stopped = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        self.stopped.set()

    def kill(self) -> None:
        self.returncode = -9
        self.stopped.set()

    def wait(self, timeout: int | None = None) -> int:
        assert self.stopped.wait(timeout)
        assert self.returncode is not None
        return self.returncode


def test_task_watchdog_kills_browser_commits_timeout_and_returns(tmp_path: Path) -> None:
    task_dir = tmp_path / "task-11"
    process = _FakeBrowserProcess()
    row = {
        "task_id": 11,
        "actions": [{"action": "evaluate", "script": "while(true){}"}],
    }

    def run_task() -> dict:
        task_dir.mkdir()
        (task_dir / ".trace.partial.json").write_text(
            json.dumps(
                [
                    {
                        "index": 0,
                        "action": "evaluate",
                        "arguments": row["actions"][0],
                        "status": "running",
                        "duration_ms": None,
                        "output": "",
                    }
                ]
            ),
            encoding="utf-8",
        )
        assert process.stopped.wait(1)
        return {"task_id": 11, "status": "fail", "error": "browser disconnected"}

    outcome, returned_process = _run_with_setup_recovery(
        task_id=11,
        task_dir=task_dir,
        process=process,
        restart_browser=lambda _current: _FakeBrowserProcess(),
        run_task=run_task,
        row=row,
        task_timeout_ms=20,
    )

    assert returned_process is process
    assert outcome["runtime_poisoned"] is True
    assert outcome["error"].startswith("ExternalTaskTimeout:")
    assert json.loads((task_dir / "result.json").read_text()) == outcome
    assert json.loads((task_dir / "trace.json").read_text())[-1]["status"] == "fail"


def test_dead_browser_restarts_before_next_task_without_replaying_result(
    tmp_path: Path,
) -> None:
    process = _FakeBrowserProcess()
    calls: list[int] = []
    restarts: list[_FakeBrowserProcess] = []

    def restart(current: _FakeBrowserProcess) -> _FakeBrowserProcess:
        restarts.append(current)
        return _FakeBrowserProcess()

    def first_task() -> dict:
        calls.append(1)
        process.returncode = 1
        return {"task_id": 1, "status": "fail"}

    first, process = _run_with_setup_recovery(
        task_id=1,
        task_dir=tmp_path / "task-1",
        process=process,
        restart_browser=restart,
        run_task=first_task,
    )
    second, process = _run_with_setup_recovery(
        task_id=2,
        task_dir=tmp_path / "task-2",
        process=process,
        restart_browser=restart,
        run_task=lambda: calls.append(2) or {"task_id": 2, "status": "pass"},
    )

    assert first["status"] == "fail"
    assert second["status"] == "pass"
    assert calls == [1, 2]
    assert len(restarts) == 1
    assert process.poll() is None


def test_setup_disconnect_restarts_and_executes_candidate_once(tmp_path: Path) -> None:
    process = _FakeBrowserProcess()
    calls = 0
    restarts = 0

    def restart(current: _FakeBrowserProcess) -> _FakeBrowserProcess:
        nonlocal restarts
        restarts += 1
        return _FakeBrowserProcess()

    def run_task() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            process.returncode = 1
            raise ConnectionError("browser closed before session setup")
        return {"task_id": 7, "status": "fail"}

    outcome, _ = _run_with_setup_recovery(
        task_id=7,
        task_dir=tmp_path / "task-7",
        process=process,
        restart_browser=restart,
        run_task=run_task,
    )

    assert outcome == {"task_id": 7, "status": "fail"}
    assert calls == 2
    assert restarts == 1


def test_existing_task_evidence_prevents_setup_retry(tmp_path: Path) -> None:
    task_dir = tmp_path / "task-9"
    process = _FakeBrowserProcess()
    calls = 0
    restarts = 0

    def restart(current: _FakeBrowserProcess) -> _FakeBrowserProcess:
        nonlocal restarts
        restarts += 1
        return _FakeBrowserProcess()

    def run_task() -> dict:
        nonlocal calls
        calls += 1
        task_dir.mkdir()
        raise RuntimeError("unexpected failure after evidence began")

    outcome, _ = _run_with_setup_recovery(
        task_id=9,
        task_dir=task_dir,
        process=process,
        restart_browser=restart,
        run_task=run_task,
    )

    assert outcome["status"] == "candidate_setup_failure"
    assert "unexpected failure" in outcome["error"]
    assert calls == 1
    assert restarts == 0


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _amendment(task_id: int, action_index: int, action: dict, updates: dict) -> dict:
    return {
        "task_id": task_id,
        "action_index": action_index,
        "precondition_action_sha256": _json_sha256(action),
        "set": updates,
    }


def test_action_overrides_apply_to_portable_and_direct_source_actions() -> None:
    evaluate = {"action": "evaluate", "script": "return true;"}
    fill = {"action": "fill", "target": {"by": "css", "value": "input"}, "text": "x"}
    portable = apply_action_overrides(
        [fill],
        [_amendment(7, 1, fill, {"wait_for_selector_hidden": ".loading-mask"})],
        task_id=7,
    )
    direct = apply_action_overrides(
        [evaluate],
        [
            _amendment(
                8,
                1,
                evaluate,
                {"script": "return false;", "script_mode": "function_body"},
            )
        ],
        task_id=8,
    )
    assert portable[0]["wait_for_selector_hidden"] == ".loading-mask"
    assert direct[0]["script_mode"] == "function_body"
    assert direct[0]["script"] == "return false;"
    assert "wait_for_selector_hidden" not in fill
    assert "script_mode" not in evaluate


def test_action_overrides_support_explicit_compiled_ref_readiness() -> None:
    click = {
        "action": "click",
        "selector": "button:visible",
        "replay_mode": "snapshot_ref_semantics",
    }
    fill = {
        "action": "fill",
        "selector": "input:visible",
        "text": "value",
        "replay_mode": "snapshot_ref_semantics",
    }

    actions = apply_action_overrides(
        [click, fill],
        [
            _amendment(7, 1, click, {"wait_for_enabled": True}),
            _amendment(7, 2, fill, {"selector": "input", "force": True}),
        ],
        task_id=7,
    )

    assert actions[0]["wait_for_enabled"] is True
    assert actions[1]["selector"] == "input"
    assert actions[1]["force"] is True


def test_action_overrides_support_non_geometric_selector_wait_state() -> None:
    wait = {
        "action": "wait",
        "selector": "div.message-success:visible",
        "timeout": 30_000,
    }

    actions = apply_action_overrides(
        [wait],
        [
            _amendment(
                464,
                1,
                wait,
                {"selector": "div.message-success", "wait_state": "attached"},
            )
        ],
        task_id=464,
    )

    assert actions[0]["selector"] == "div.message-success"
    assert actions[0]["wait_state"] == "attached"


def test_action_overrides_reject_wrong_hash_unknown_field_and_bad_index() -> None:
    action = {"action": "evaluate", "script": "return true;"}
    wrong_hash = _amendment(9, 1, action, {"script_mode": "function_body"})
    wrong_hash["precondition_action_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="precondition"):
        apply_action_overrides([action], [wrong_hash], task_id=9)
    with pytest.raises(ValueError, match="invalid"):
        apply_action_overrides(
            [action], [_amendment(9, 1, action, {"task_specific_delay": 2})], task_id=9
        )
    with pytest.raises(ValueError, match="outside"):
        apply_action_overrides(
            [action], [_amendment(9, 2, action, {"script_mode": "function_body"})], task_id=9
        )
    fill = {"action": "fill", "text": "x"}
    with pytest.raises(ValueError, match="script fields require an evaluate action"):
        apply_action_overrides(
            [fill], [_amendment(9, 1, fill, {"script": "return true;"})], task_id=9
        )


@pytest.mark.parametrize("script", ["", "   ", "x" * (64 * 1024 + 1)])
def test_action_overrides_reject_invalid_replacement_scripts(script: str) -> None:
    action = {"action": "evaluate", "script": "return true;"}
    with pytest.raises(ValueError, match="script is invalid"):
        apply_action_overrides(
            [action], [_amendment(9, 1, action, {"script": script})], task_id=9
        )


@pytest.mark.parametrize(
    ("catalog", "row_source_sha256"),
    [
        ({}, None),
        ({"action_overrides_sha256": None}, None),
        ({"action_overrides_sha256": "invalid"}, "invalid"),
        ({"action_overrides_sha256": "A" * 64}, "A" * 64),
    ],
)
def test_row_action_overrides_require_valid_catalog_source_hash(
    catalog: dict, row_source_sha256: object
) -> None:
    action = {"action": "evaluate", "script": "return true;"}
    amendments = [_amendment(9, 1, action, {"script_mode": "function_body"})]
    row = {
        "task_id": 9,
        "action_overrides": {
            "schema": "lex_webarena_replay_action_overrides/1",
            "source_sha256": row_source_sha256,
            "amendments_sha256": _json_sha256(amendments),
            "amendments": amendments,
        },
    }
    with pytest.raises(ValueError, match="provenance is invalid"):
        catalog_action_overrides(catalog, row)


def test_row_action_overrides_reject_empty_amendment_binding() -> None:
    source_sha256 = "a" * 64
    row = {
        "task_id": 9,
        "action_overrides": {
            "schema": "lex_webarena_replay_action_overrides/1",
            "source_sha256": source_sha256,
            "amendments_sha256": _json_sha256([]),
            "amendments": [],
        },
    }
    with pytest.raises(ValueError, match="provenance changed"):
        catalog_action_overrides({"action_overrides_sha256": source_sha256}, row)


def test_external_action_overrides_reject_empty_amendment_binding(tmp_path: Path) -> None:
    path = tmp_path / "empty-overrides.json"
    _write(
        path,
        {"schema": "lex_webarena_replay_action_overrides/1", "amendments": []},
    )
    with pytest.raises(ValueError, match="at least one amendment"):
        load_action_overrides(path)


def test_catalog_requires_one_hash_bound_row_in_declared_order(tmp_path: Path) -> None:
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
        for task_id in range(3)
    ]
    catalog_path = tmp_path / "catalog.json"
    catalog = {
        "schema": "lex_webarena_full_replay_catalog/1",
        "task_ids": list(range(3)),
        "site_map_sha256": sites_hash,
        "portable_suite_sha256": suite_hash,
        "rows": rows,
    }
    _write(catalog_path, catalog)
    loaded, _sites, tasks = load_catalog_inputs(catalog_path, suite_path, sites_path)
    assert len(loaded["rows"]) == 3
    assert set(tasks) == {0}

    catalog["rows"][2]["recorded_response"] = {"status": "SUCCESS"}
    _write(catalog_path, catalog)
    with pytest.raises(ValueError, match="recorded answers"):
        load_catalog_inputs(catalog_path, suite_path, sites_path)


def test_catalog_binding_embeds_override_provenance_and_hashes_amended_actions(
    tmp_path: Path,
) -> None:
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
    portable_action = {"action": "fill", "target": {"by": "css", "value": "input"}, "text": "x"}
    direct_action = {"action": "evaluate", "script": "return true;"}
    suite_path = tmp_path / "suite.json"
    suite_hash = _write(
        suite_path,
        {
            "schema": "lex_browser_use_tasks/4",
            "tasks": [
                {
                    "id": "webarena-0",
                    "title": "Portable",
                    "difficulty": "simple",
                    "tags": ["webarena"],
                    "trajectory": {
                        "schema": "lex_browser_trajectory/1",
                        "actions": [portable_action],
                    },
                    "assertions": [],
                    "evaluator": {"kind": "webarena_verified", "task_id": 0, "revision": 2},
                    "environment": {"kind": "webarena_verified"},
                    "ground_truth": {
                        "oracle": "webarena_verified",
                        "expected_actions": ["fill"],
                    },
                    "source": {"url": "https://example.test"},
                }
            ],
        },
    )
    catalog_path = tmp_path / "catalog.json"
    _write(
        catalog_path,
        {
            "schema": "lex_webarena_replay_catalog/1",
            "task_ids": [0, 1],
            "site_map_sha256": sites_hash,
            "portable_suite_sha256": suite_hash,
            "rows": [
                {
                    "task_id": 0,
                    "mode": "portable",
                    "bound_calls_sha256": _json_sha256([portable_action]),
                },
                {
                    "task_id": 1,
                    "mode": "direct",
                    "source_actions": [direct_action],
                    "bound_calls_sha256": _json_sha256([direct_action]),
                },
            ],
        },
    )
    overrides_path = tmp_path / "overrides.json"
    _write(
        overrides_path,
        {
            "schema": "lex_webarena_replay_action_overrides/1",
            "amendments": [
                _amendment(0, 1, portable_action, {"wait_for_selector_hidden": ".loading-mask"}),
                _amendment(1, 1, direct_action, {"script_mode": "function_body"}),
            ],
        },
    )

    rebound = rebind_catalog(
        catalog_path, suite_path, sites_path, action_overrides_path=overrides_path
    )
    rows = {row["task_id"]: row for row in rebound["rows"]}
    assert rebound["action_overrides_sha256"] == file_sha256(overrides_path)
    assert rows[0]["bound_calls_sha256"] == _json_sha256(
        [{**portable_action, "wait_for_selector_hidden": ".loading-mask"}]
    )
    assert rows[1]["bound_calls_sha256"] == _json_sha256(
        [{**direct_action, "script_mode": "function_body"}]
    )
    assert rows[0]["action_overrides"]["source_sha256"] == file_sha256(overrides_path)

    rebound_path = tmp_path / "rebound.json"
    _write(rebound_path, rebound)
    loaded, _, _ = load_catalog_inputs(
        rebound_path, suite_path, sites_path, action_overrides_path=overrides_path
    )
    assert loaded["rows"] == rebound["rows"]

    for top_hash in ("missing", None, "invalid"):
        changed = json.loads(json.dumps(rebound))
        if top_hash == "missing":
            changed.pop("action_overrides_sha256")
        else:
            changed["action_overrides_sha256"] = top_hash
        _write(rebound_path, changed)
        with pytest.raises(ValueError, match="external action overrides source"):
            load_catalog_inputs(rebound_path, suite_path, sites_path)
        with pytest.raises(ValueError, match="not bound to the action overrides"):
            load_catalog_inputs(
                rebound_path,
                suite_path,
                sites_path,
                action_overrides_path=overrides_path,
            )

    _write(rebound_path, rebound)
    with pytest.raises(ValueError, match="rebind requires its external action overrides"):
        rebind_catalog(rebound_path, suite_path, sites_path)

    revised_overrides_path = tmp_path / "revised-overrides.json"
    _write(
        revised_overrides_path,
        {
            "schema": "lex_webarena_replay_action_overrides/1",
            "amendments": [
                _amendment(0, 1, portable_action, {"wait_for_selector_hidden": ".loading-mask"}),
                _amendment(1, 1, direct_action, {"script": "return false;"}),
            ],
        },
    )
    with pytest.raises(ValueError, match="differ from the predecessor"):
        rebind_catalog(
            rebound_path,
            suite_path,
            sites_path,
            action_overrides_path=revised_overrides_path,
        )
    revised = rebind_catalog(
        rebound_path,
        suite_path,
        sites_path,
        action_overrides_path=revised_overrides_path,
        revise_action_overrides=True,
    )
    assert revised["predecessor_catalog_sha256"] == file_sha256(rebound_path)
    assert revised["action_overrides_sha256"] == file_sha256(revised_overrides_path)
    assert revised["repairs"][-1] == {
        "kind": "action_override_revision",
        "predecessor_action_overrides_sha256": file_sha256(overrides_path),
        "action_overrides_sha256": file_sha256(revised_overrides_path),
        "task_ids": [0, 1],
    }
    revised_path = tmp_path / "revised-catalog.json"
    _write(revised_path, revised)
    load_catalog_inputs(
        revised_path,
        suite_path,
        sites_path,
        action_overrides_path=revised_overrides_path,
    )

    wrong_overrides_path = tmp_path / "wrong-overrides.json"
    wrong_overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    wrong_overrides["amendments"].reverse()
    _write(wrong_overrides_path, wrong_overrides)
    with pytest.raises(ValueError, match="not bound to the action overrides"):
        load_catalog_inputs(
            rebound_path,
            suite_path,
            sites_path,
            action_overrides_path=wrong_overrides_path,
        )


@pytest.mark.parametrize("top_hash", ["missing", None])
def test_runner_rejects_row_overrides_without_external_version_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, top_hash: object
) -> None:
    catalog = {"rows": [{"task_id": 9, "action_overrides": {"amendments": []}}]}
    if top_hash != "missing":
        catalog["action_overrides_sha256"] = top_hash
    monkeypatch.setattr(catalog_runner, "verify_webarena_runtime_source", lambda: None)
    monkeypatch.setattr(
        catalog_runner,
        "load_catalog_inputs",
        lambda *args, **kwargs: (catalog, {}, {}),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--catalog",
            str(tmp_path / "catalog.json"),
            "--portable-suite",
            str(tmp_path / "suite.json"),
            "--site-map",
            str(tmp_path / "sites.json"),
            "--site-reset-receipt",
            str(tmp_path / "receipt.json"),
            "--environment-profile",
            str(tmp_path / "environment.json"),
            "--binary",
            str(tmp_path / "browser"),
            "--backend",
            "playwright",
            "--output",
            str(tmp_path / "output"),
        ],
    )
    with pytest.raises(ValueError, match="requires its versioned action overrides"):
        catalog_runner.main()


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
