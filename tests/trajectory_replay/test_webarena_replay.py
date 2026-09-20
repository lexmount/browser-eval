import ast
import inspect
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_dataset import OBSERVATION_PROTOCOL
from harness.trajectory_replay.webarena_replay import (
    RECEIPT_SCHEMA,
    _attempt_needs_retry,
    _capture_failure_ui_state,
    _click_was_dispatched_before_navigation_timeout,
    _embedded_observation_error,
    _execute_replay_call,
    _finalize_method_teardown,
    _origin,
    _rebind_url,
    _reject_partial_method_resume,
    _run_one,
    _validated_resume,
    run_replay_suite,
    select_methods,
    select_tasks,
    validate_qualification_dataset,
)


def test_method_scoped_site_cohort_rejects_partial_resume() -> None:
    with pytest.raises(ValueError, match="cannot resume after a partial method"):
        _reject_partial_method_resume([("moli", 1)], ["moli"], [1, 2])
    _reject_partial_method_resume([("moli", 1), ("moli", 2)], ["moli"], [1, 2])
    _reject_partial_method_resume([("chromium", 1), ("chromium", 2)],
                                  ["chromium", "moli"], [1, 2])


def test_failure_ui_observation_bounds_selector_and_excludes_form_values() -> None:
    class Page:
        def evaluate(self, script: str, selector: str | None) -> dict[str, object]:
            assert "getBoundingClientRect" in script
            assert "getComputedStyle" in script
            assert "document.querySelectorAll(nativeSelector)" not in script
            assert "element.value" not in script
            return {"selector": selector, "region_controls": []}

        def locator(self, selector):
            class Locator:
                def evaluate_all(self, script):
                    assert "element.value" not in script
                    assert "getBoundingClientRect" in script
                    return {"selector_count": 1, "matched_controls": []}
            return Locator()

    class Service:
        _page = Page()

        def _submit(self, callback):
            return callback()

    service = Service()
    assert _capture_failure_ui_state(service, {"selector": "select[name=region_id]"})[
        "selector"
    ] == "select[name=region_id]"
    assert _capture_failure_ui_state(service, {"selector": "select[name=region_id]:visible"})[
        "selector"
    ] == "select[name=region_id]:visible"
    assert _capture_failure_ui_state(service, {"selector": "x" * 1001})[
        "selector"
    ] is None


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

    assert '"qualification_shard" if qualification_task_ids is not None' in source
    assert "qualification and diagnostic scopes cannot be combined" in source
    assert "qualification engine differs from the frozen reference engine" in source
    assert 'scope = "formal_correction_shard"' in source
    assert 'scope in {"formal", "formal_correction_shard"}' in source
    assert "formal correction shards must cover every registered browser" in source


def test_only_non_promotable_diagnostics_may_use_a_dirty_harness_checkout() -> None:
    source = inspect.getsource(run_replay_suite)

    assert 'if scope != "diagnostic"' in source
    assert '"git", "-C", str(repo_root), "rev-parse", "HEAD"' in source
    assert "cowagent_commit = _require_clean_git_checkout(cowagent)" in source


def test_replay_cleanup_and_locking_preserve_run_isolation() -> None:
    source = inspect.getsource(run_replay_suite)
    run_one_source = inspect.getsource(_run_one)

    assert "_docker_daemon_lock_namespace(docker_info)" in source
    assert 'cleanup_errors.append(f"resource sampler stop failed: {exc}")' in run_one_source
    assert 'cleanup_errors.append(f"browser tool close failed: {exc}")' in run_one_source
    assert '"cleanup_errors": cleanup_errors' in run_one_source


def test_method_teardown_failure_does_not_mask_primary_error(monkeypatch, tmp_path: Path) -> None:
    primary = RuntimeError("task failed")
    receipt = {}
    state = {"chromium": False}
    monkeypatch.setattr(
        "harness.trajectory_replay.webarena_replay._teardown_method",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )
    monkeypatch.setattr(
        "harness.trajectory_replay.webarena_replay._write_receipt",
        lambda *_args: None,
    )

    _finalize_method_teardown(
        object(), "run", "chromium", state, receipt, tmp_path / "receipt.json", primary
    )

    assert state == {"chromium": False}
    assert receipt["method_teardown_errors"]["chromium"] == "cleanup failed"
    assert "cleanup failed" in " ".join(primary.__notes__)


def test_method_teardown_failure_is_raised_without_primary_error(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "harness.trajectory_replay.webarena_replay._teardown_method",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )
    monkeypatch.setattr(
        "harness.trajectory_replay.webarena_replay._write_receipt",
        lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        _finalize_method_teardown(
            object(),
            "run",
            "chromium",
            {"chromium": False},
            {},
            tmp_path / "receipt.json",
            None,
        )


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
                    "candidates": [{"id": "candidate-1", "tool_calls": [{"action": "snapshot"}]}],
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
                    "observation_protocol": OBSERVATION_PROTOCOL,
                    "seed_manifest_sha256": "seed-hash",
                    "profile_sha256": "profile-hash",
                },
                "trajectories": [{"task_id": 1, "tool_calls": [{"action": "snapshot"}]}],
            }
        ),
        encoding="utf-8",
    )

    assert len(validate_qualification_dataset(plan, dataset)) == 64
    from types import SimpleNamespace

    plan["state_contracts"] = {1: SimpleNamespace(definition_sha256="current-state")}
    with pytest.raises(ValueError, match="legacy qualification"):
        validate_qualification_dataset(plan, dataset)
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    payload["trajectories"][0]["verification"] = {"state_contract_sha256": "current-state"}
    dataset.write_text(json.dumps(payload), encoding="utf-8")
    assert len(validate_qualification_dataset(plan, dataset)) == 64
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    payload["provenance"].pop("observation_protocol")
    dataset.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="valid-observation qualification"):
        validate_qualification_dataset(plan, dataset)
    payload["provenance"]["observation_protocol"] = OBSERVATION_PROTOCOL
    payload["trajectories"][0]["tool_calls"] = [{"action": "wait", "seconds": 1}]
    dataset.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical input"):
        validate_qualification_dataset(plan, dataset)


def test_qualification_binds_retrieval_protocol_per_task(tmp_path: Path) -> None:
    from harness.trajectory_replay.retrieval_acceptance import PROTOCOL, PROTOCOL_V2, digest

    contracts = {1: {"schema": PROTOCOL}, 2: {"schema": PROTOCOL_V2}}
    tasks = [
        {
            "task_id": task_id,
            "canonical_candidate_id": f"candidate-{task_id}",
            "candidates": [{"id": f"candidate-{task_id}", "tool_calls": [{"action": "snapshot"}]}],
        }
        for task_id in (1, 2)
    ]
    plan = {
        "seed_manifest_sha256": "seed-hash",
        "profile_sha256": "profile-hash",
        "qualification_engine": "chromium",
        "task_ids": [1, 2],
        "retrieval_task_ids": [1, 2],
        "retrieval_contracts": contracts,
        "seed_manifest": {"tasks": tasks},
    }
    payload = {
        "schema": "lex_webarena_verified_trajectory_dataset/1",
        "promotion_policy": {"qualification_browser": "chromium"},
        "provenance": {
            "observation_protocol": OBSERVATION_PROTOCOL,
            "seed_manifest_sha256": "seed-hash",
            "profile_sha256": "profile-hash",
            "retrieval_protocols": {"1": PROTOCOL, "2": PROTOCOL_V2},
        },
        "trajectories": [
            {
                "task_id": task_id,
                "tool_calls": [{"action": "snapshot"}],
                "verification": {
                    "retrieval_protocol": contracts[task_id]["schema"],
                    "retrieval_contract_sha256": digest(contracts[task_id]),
                },
            }
            for task_id in (1, 2)
        ],
    }
    dataset = tmp_path / "mixed-retrieval-trajectories.json"
    dataset.write_text(json.dumps(payload), encoding="utf-8")
    assert len(validate_qualification_dataset(plan, dataset)) == 64
    payload["trajectories"][1]["verification"]["retrieval_protocol"] = PROTOCOL
    dataset.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="legacy qualification"):
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
    assert _rebind_url("https://example.com:8023/path", "192.168.9.2", {"example.com"}) == (
        "https://192.168.9.2:8023/path"
    )
    assert (
        _rebind_url("https://attacker.invalid:8023/path", "192.168.9.2", {"example.com"})
        == "https://attacker.invalid:8023/path"
    )
    assert _rebind_url("https://example.com/path", "192.168.9.2", {"example.com"}) == (
        "https://example.com/path"
    )


def test_origin_removes_environment_paths_before_storage_reset() -> None:
    assert _origin("http://192.168.5.2:7780/admin") == "http://192.168.5.2:7780"


def test_every_failed_logical_replay_gets_one_clean_retry() -> None:
    assert _attempt_needs_retry(
        {
            "status": "fail",
            "process_status": "fail",
            "evaluator_status": "not_run",
            "completed_action_count": 4,
        }
    )


def test_snapshot_payload_error_is_exposed_as_observation_error() -> None:
    assert (
        _embedded_observation_error(
            {"action": "snapshot"},
            "success",
            "[Snapshot error: Page.evaluate: Execution context was destroyed]",
        )
        == "Page.evaluate: Execution context was destroyed"
    )
    assert (
        _embedded_observation_error({"action": "click"}, "success", "[Snapshot error: ignored]")
        is None
    )
    assert _embedded_observation_error({"action": "snapshot"}, "success", "Page: stable") is None
    assert _attempt_needs_retry(
        {
            "status": "fail",
            "process_status": "pass",
            "evaluator_status": "fail",
        }
    )
    assert not _attempt_needs_retry(
        {
            "status": "pass",
            "process_status": "pass",
            "evaluator_status": "pass",
        }
    )


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


@pytest.mark.parametrize("collect_diagnostics", [False, True])
@pytest.mark.parametrize("disabled", [False, True])
def test_compiled_snapshot_ref_click_preserves_dom_click_semantics(collect_diagnostics, disabled) -> None:
    events = []

    class Locator:
        first = None

        def __init__(self) -> None:
            self.first = self

        def evaluate(self, script, *args):
            events.append(("evaluate", script, args))
            return {"connected": True, "disabled": disabled,
                    "effectivelyDisabled": disabled, "ariaDisabled": None}

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

    diagnostics = {} if collect_diagnostics else None
    status, _result = _execute_replay_call(
        Tool(),
        {
            "action": "click",
            "selector": "a[href='/next']:visible",
            "replay_mode": "snapshot_ref_semantics",
        },
        diagnostics=diagnostics,
    )

    assert status == "success"
    assert events[0] == ("locator", "a[href='/next']:visible")
    assert events[-1] == ("wait", 500)
    evaluations = [event for event in events if event[0] == "evaluate"]
    assert len(evaluations) == 1
    assert "element.click()" in evaluations[0][1]
    if collect_diagnostics:
        assert diagnostics["click_dispatch_state"]["disabled"] is disabled
        assert evaluations[0][1].index("disabled:") < evaluations[0][1].index("element.click()")


@pytest.mark.parametrize(
    ("platform", "modifier"),
    [("Win32", "Control"), ("MacIntel", "Meta"), ("Linux x86_64", "Control")],
)
def test_compiled_snapshot_ref_fill_and_select_preserve_dom_semantics(
    platform: str, modifier: str
) -> None:
    events = []

    class Locator:
        first = None

        def __init__(self) -> None:
            self.first = self

        def evaluate(self, script, *args):
            events.append(("evaluate", script, args))
            return platform if "navigator.platform" in script else None

    class Keyboard:
        def press(self, key):
            events.append(("press", key))

        def insert_text(self, text):
            events.append(("insert_text", text))

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
    assert events.index(("press", f"{modifier}+A")) < events.index(("press", "Backspace"))
    assert events.index(("press", "Backspace")) < events.index(("insert_text", "needle"))
    assert not any(event[0] == "evaluate" and "element.value = ''" in event[1] for event in events)
    assert any(event[0] == "evaluate" and event[2] == ("open",) for event in events)


def test_declared_wait_seconds_are_dispatched_as_browser_tool_milliseconds() -> None:
    calls = []

    class Result:
        status = "success"
        result = "waited"

    class Tool:
        def execute(self, arguments):
            calls.append(arguments)
            return Result()

    status, result = _execute_replay_call(Tool(), {"action": "wait", "seconds": 20})

    assert (status, result) == ("success", "waited")
    assert calls == [{"action": "wait", "timeout": 20_000}]


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
        "seed_manifest": {"tasks": [{"task_id": 1, "canonical_candidate_id": "canonical-1"}]},
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
        "seed_manifest": {"tasks": [{"task_id": 1, "canonical_candidate_id": "canonical-1"}]},
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
        "profile": {"execution": {"reset_before_every_task": False}},
        "profile_sha256": "profile",
        "seed_manifest_sha256": "seeds",
        "seed_manifest": {"tasks": [{"task_id": 1, "canonical_candidate_id": "canonical-1"}]},
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


@pytest.mark.parametrize("corrupt_section", ["attempts", "physical_executions"])
def test_resume_reports_missing_task_id_as_invalid_receipt(
    tmp_path: Path, corrupt_section: str
) -> None:
    runtime = {"resources": {"cpus": 2.0}}
    plan = {
        "profile": {"execution": {"reset_before_every_task": False}},
        "profile_sha256": "profile",
        "seed_manifest_sha256": "seeds",
        "seed_manifest": {"tasks": [{"task_id": 1, "canonical_candidate_id": "canonical-1"}]},
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
        "attempts": [],
        "method_teardown": {"chromium": True},
        "sessions": [],
        "physical_executions": [],
    }
    if corrupt_section == "attempts":
        receipt["attempts"] = [
            {
                "schema": "lex_webarena_trajectory_replay_attempt/1",
                "engine": "chromium",
                "source_candidate_id": "canonical-1",
            }
        ]
    else:
        receipt["physical_executions"] = [
            {
                "engine": "chromium",
                "physical_execution": 1,
                "status": "complete",
            }
        ]
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="omitted task_id"):
        _validated_resume(
            receipt_path,
            plan=plan,
            task_ids=[1],
            engines=["chromium"],
            scope="formal",
            runtime=runtime,
        )
