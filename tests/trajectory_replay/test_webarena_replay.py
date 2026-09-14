import ast
import inspect
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_replay import (
    RECEIPT_SCHEMA,
    _browser_context_needs_restart,
    _click_was_dispatched_before_navigation_timeout,
    _execute_replay_call,
    _origin,
    _rebind_url,
    _validated_resume,
    run_replay_suite,
    select_methods,
    select_tasks,
    validate_qualification_dataset,
)


def test_select_tasks_requires_clean_canonical_candidates() -> None:
    manifest = {
        "tasks": [
            {"task_id": 1, "replay_status": "ready"},
            {"task_id": 2, "replay_status": "all_methods_failed"},
        ]
    }

    assert [task["task_id"] for task in select_tasks(manifest, [1])] == [1]


def test_select_methods_limits_diagnostic_run_to_requested_engine() -> None:
    plan = {"methods": [{"engine": "chromium"}, {"engine": "moli"}]}

    assert select_methods(plan, ["chromium"]) == [{"engine": "chromium"}]
    assert select_methods(plan, ["chromium", "moli"]) == plan["methods"]


def test_select_methods_rejects_profile_order_mismatch() -> None:
    plan = {"methods": [{"engine": "moli"}, {"engine": "chromium"}]}

    with pytest.raises(ValueError, match="selected engine scope"):
        select_methods(plan, ["chromium", "moli"])


def test_qualification_scope_is_separate_from_diagnostics() -> None:
    source = inspect.getsource(run_replay_suite)

    assert 'scope = "qualification"' in source
    assert "qualification and diagnostic scopes cannot be combined" in source
    assert "qualification engine differs from the frozen reference engine" in source


def test_formal_dataset_binding_requires_all_canonical_trajectories(tmp_path: Path) -> None:
    plan = {
        "seed_manifest_sha256": "seed-hash",
        "profile_sha256": "profile-hash",
        "qualification_engine": "chromium",
        "task_ids": [1],
        "seed_manifest": {
            "tasks": [
                {
                    "task_id": 1,
                    "canonical_candidate_id": "candidate-1",
                    "candidates": [
                        {"id": "candidate-1", "tool_calls": [{"action": "snapshot"}]}
                    ],
                }
            ]
        },
    }
    dataset = tmp_path / "trajectories.json"
    dataset.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_verified_trajectory_dataset/1",
                "promotion_policy": {"qualification_browser": "chromium"},
                "provenance": {
                    "seed_manifest_sha256": "seed-hash",
                    "profile_sha256": "profile-hash",
                },
                "trajectories": [
                    {"task_id": 1, "tool_calls": [{"action": "snapshot"}]}
                ],
            }
        ),
        encoding="utf-8",
    )

    assert len(validate_qualification_dataset(plan, dataset)) == 64
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    payload["trajectories"][0]["tool_calls"] = [{"action": "wait", "seconds": 1}]
    dataset.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical input"):
        validate_qualification_dataset(plan, dataset)


def test_rebind_url_changes_only_known_local_site_ports() -> None:
    assert (
        _rebind_url(
            "http://192.168.5.2:8023/group/project/-/issues?state=opened",
            "192.168.9.2",
            {"192.168.5.2"},
        )
        == "http://192.168.9.2:8023/group/project/-/issues?state=opened"
    )
    assert _rebind_url(
        "https://example.com:8023/path", "192.168.9.2", {"example.com"}
    ) == ("https://192.168.9.2:8023/path")
    assert _rebind_url(
        "https://attacker.invalid:8023/path", "192.168.9.2", {"example.com"}
    ) == "https://attacker.invalid:8023/path"
    assert _rebind_url(
        "https://example.com/path", "192.168.9.2", {"example.com"}
    ) == ("https://example.com/path")


def test_origin_removes_environment_paths_before_storage_reset() -> None:
    assert _origin("http://192.168.5.2:7780/admin") == "http://192.168.5.2:7780"


def test_browser_context_restart_is_limited_to_pre_action_context_failure() -> None:
    failed_setup = {
        "process_status": "fail",
        "completed_action_count": 0,
        "error": "Protocol error: BrowserContextNotLoaded",
    }
    assert _browser_context_needs_restart(failed_setup)
    assert not _browser_context_needs_restart({**failed_setup, "completed_action_count": 1})
    assert not _browser_context_needs_restart({**failed_setup, "error": "navigation failed"})
    assert not _browser_context_needs_restart({**failed_setup, "process_status": "pass"})


def test_completed_click_is_not_replayed_when_only_navigation_wait_times_out() -> None:
    error = """Click failed: Page.click: Timeout 5000ms exceeded.
    - performing click action
    - click action done
    - waiting for scheduled navigations to finish"""

    assert _click_was_dispatched_before_navigation_timeout(
        {"action": "click", "selector": "a[href='/next']"}, "error", error
    )
    assert not _click_was_dispatched_before_navigation_timeout(
        {"action": "click", "selector": "a[href='/next']"},
        "error",
        "Click failed: Page.click: Timeout 5000ms exceeded; waiting for locator",
    )
    assert not _click_was_dispatched_before_navigation_timeout(
        {"action": "fill", "selector": "input"}, "error", error
    )


def test_compiled_snapshot_ref_click_preserves_dom_click_semantics() -> None:
    events = []

    class Locator:
        first = None

        def __init__(self) -> None:
            self.first = self

        def evaluate(self, script, *args):
            events.append(("evaluate", script, args))

    class Page:
        def locator(self, selector):
            events.append(("locator", selector))
            return Locator()

        def wait_for_timeout(self, timeout):
            events.append(("wait", timeout))

    class Service:
        _page = Page()

        def _submit(self, callback):
            return callback()

    class Tool:
        def _get_service(self):
            return Service()

        def execute(self, _arguments):
            raise AssertionError("compiled ref call must not use Playwright actionability click")

    status, _result = _execute_replay_call(
        Tool(),
        {
            "action": "click",
            "selector": "a[href='/next']:visible",
            "replay_mode": "snapshot_ref_semantics",
        },
    )

    assert status == "success"
    assert events[0] == ("locator", "a[href='/next']:visible")
    assert events[-1] == ("wait", 500)


def test_compiled_snapshot_ref_fill_and_select_preserve_dom_semantics() -> None:
    events = []

    class Locator:
        first = None

        def __init__(self) -> None:
            self.first = self

        def evaluate(self, script, *args):
            events.append(("evaluate", script, args))

    class Keyboard:
        def type(self, text):
            events.append(("type", text))

    class Page:
        keyboard = Keyboard()

        def locator(self, selector):
            events.append(("locator", selector))
            return Locator()

    class Service:
        _page = Page()

        def _submit(self, callback):
            return callback()

    class Tool:
        def _get_service(self):
            return Service()

    fill_status, _ = _execute_replay_call(
        Tool(),
        {
            "action": "fill",
            "selector": "input[name='query']:visible",
            "text": "needle",
            "replay_mode": "snapshot_ref_semantics",
        },
    )
    select_status, _ = _execute_replay_call(
        Tool(),
        {
            "action": "select",
            "selector": "select[name='status']:visible",
            "value": "open",
            "replay_mode": "snapshot_ref_semantics",
        },
    )

    assert fill_status == select_status == "success"
    assert ("type", "needle") in events
    assert any(event[0] == "evaluate" and event[2] == ("open",) for event in events)


def test_formal_orchestration_passes_trusted_hosts_only_to_replay_calls() -> None:
    tree = ast.parse(inspect.getsource(run_replay_suite))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    run_one_calls = [
        node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "_run_one"
    ]
    journal_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "_start_physical_execution"
    ]

    assert len(run_one_calls) == len(journal_calls) == 2
    assert all(
        "trusted_source_site_hosts" in {keyword.arg for keyword in node.keywords}
        for node in run_one_calls
    )
    assert all(
        "trusted_source_site_hosts" not in {keyword.arg for keyword in node.keywords}
        for node in journal_calls
    )


def test_resume_rejects_noncanonical_or_out_of_plan_attempts(tmp_path: Path) -> None:
    runtime = {"resources": {"cpus": 2.0}}
    plan = {
        "profile_sha256": "profile",
        "seed_manifest_sha256": "seeds",
        "seed_manifest": {
            "tasks": [{"task_id": 1, "canonical_candidate_id": "canonical-1"}]
        },
    }
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "execution_scope": "formal",
        "profile_sha256": "profile",
        "source_seed_manifest_sha256": "seeds",
        "task_ids": [1],
        "engines": ["chromium"],
        "runtime": runtime,
        "run_id": "run",
        "attempts": [
            {
                "schema": "lex_webarena_trajectory_replay_attempt/1",
                "engine": "chromium",
                "task_id": 1,
                "source_candidate_id": "wrong",
            }
        ],
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="non-canonical"):
        _validated_resume(
            receipt_path,
            plan=plan,
            task_ids=[1],
            engines=["chromium"],
            scope="formal",
            runtime=runtime,
        )


def test_resume_rejects_changed_runtime_binding(tmp_path: Path) -> None:
    plan = {
        "profile_sha256": "profile",
        "seed_manifest_sha256": "seeds",
        "seed_manifest": {
            "tasks": [{"task_id": 1, "canonical_candidate_id": "canonical-1"}]
        },
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": RECEIPT_SCHEMA,
                "execution_scope": "formal",
                "profile_sha256": "profile",
                "source_seed_manifest_sha256": "seeds",
                "task_ids": [1],
                "engines": ["chromium"],
                "runtime": {"resources": {"cpus": 1.0}},
                "run_id": "run",
                "attempts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime"):
        _validated_resume(
            receipt_path,
            plan=plan,
            task_ids=[1],
            engines=["chromium"],
            scope="formal",
            runtime={"resources": {"cpus": 2.0}},
        )


def test_resume_rejects_interrupted_physical_execution(tmp_path: Path) -> None:
    runtime = {"resources": {"cpus": 2.0}}
    plan = {
        "profile_sha256": "profile",
        "seed_manifest_sha256": "seeds",
        "seed_manifest": {
            "tasks": [{"task_id": 1, "canonical_candidate_id": "canonical-1"}]
        },
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema": RECEIPT_SCHEMA,
                "execution_scope": "formal",
                "profile_sha256": "profile",
                "source_seed_manifest_sha256": "seeds",
                "task_ids": [1],
                "engines": ["chromium"],
                "runtime": runtime,
                "run_id": "run",
                "attempts": [],
                "method_teardown": {"chromium": False},
                "sessions": [],
                "physical_executions": [
                    {
                        "engine": "chromium",
                        "task_id": 1,
                        "physical_execution": 1,
                        "status": "running",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="interrupted physical execution"):
        _validated_resume(
            receipt_path,
            plan=plan,
            task_ids=[1],
            engines=["chromium"],
            scope="formal",
            runtime=runtime,
        )
