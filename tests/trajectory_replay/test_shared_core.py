"""Both replay tracks share one core, and keep their own result and schema contracts."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from harness.trajectory_replay import runner as curated_runner
from harness.trajectory_replay.core import docker_runner
from harness.trajectory_replay.core.executor import body_text, judge_outputs, validate_outputs
from harness.trajectory_replay.core.suite import BrowserTask
from harness.trajectory_replay.metrics import summarize_attempts as summarize_curated
from harness.trajectory_replay.public_webarena import runner as public_runner
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


def test_tracks_keep_their_own_conditional_latency_when_a_retry_passes_after_a_failure():
    rows = [_attempt("a", 1, "fail", 100.0), _attempt("a", 2, "pass", 11.0)]

    curated = summarize_curated(rows, engines=("a",))["functional"]["a"]
    public = summarize_public(rows, engines=("a",))["functional"]["a"]

    assert (curated["final_passes"], curated["duration_ms"]["n"]) == (1, 0)
    assert (public["final_passes"], public["duration_ms"]["n"]) == (1, 1)
    assert public["duration_ms"]["mean"] == 11.0


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


def _browser_task(**fields) -> BrowserTask:
    values = {
        "task_id": "task",
        "title": "Task",
        "tool_calls": ({"action": "navigate", "url": "{fixture_origin}/a.html", "timeout_ms": 5},),
        "assertions": (),
        "tags": ("retrieve",),
        "difficulty": "simple",
        "ground_truth": {"expected_actions": ["navigate"]},
        "source": {"kind": "test"},
        **fields,
    }
    return BrowserTask(**values)


def test_exact_ground_truth_judge_reports_expected_and_actual():
    assertions = [{"output": "answer", "equals": "unique-73"}]

    assert validate_outputs({"answer": "unique-73"}, assertions) == []
    assert "exact mismatch" in validate_outputs({"answer": "near-73"}, assertions)[0]
    assert judge_outputs({"answer": "unique-73"}, assertions)[0]["passed"] is True


def test_http_origin_keeps_only_scheme_and_authority():
    assert docker_runner.http_origin("https://example.com:8443/a?q=1") == (
        "https://example.com:8443"
    )
    assert docker_runner.http_origin("about:blank") is None


def test_origin_recorder_keeps_every_top_level_http_origin_for_the_next_reset():
    page = MagicMock()
    visited: set[str] = set()
    record = docker_runner.main_frame_origin_recorder(page, visited)

    record(MagicMock(url="https://subframe.test/embed"))
    assert visited == set()

    page.main_frame.url = "about:blank"
    record(page.main_frame)
    assert visited == set()

    page.main_frame.url = "https://first.test:8443/a?q=1"
    record(page.main_frame)
    page.main_frame.url = "https://second.test/b"
    record(page.main_frame)
    assert visited == {"https://first.test:8443", "https://second.test"}


def test_materializing_a_task_replaces_only_the_fixture_origin():
    task = _browser_task(
        evaluator={"kind": "exact"}, environment={"kind": "none"}, trajectory={"actions": []}
    )

    result = docker_runner.materialize_task(task, "http://fixtures.test")

    assert result.tool_calls == (
        {"action": "navigate", "url": "http://fixtures.test/a.html", "timeout_ms": 5},
    )
    assert task.tool_calls[0]["url"] == "{fixture_origin}/a.html"
    assert (result.evaluator, result.environment, result.trajectory) == (
        task.evaluator,
        task.environment,
        task.trajectory,
    )


@pytest.mark.parametrize(
    ("actions", "statuses", "passed"),
    [
        (["navigate"], ["pass"], True),
        (["navigate"], ["fail"], False),
        (["click"], ["pass"], False),
        ([], [], False),
    ],
)
def test_process_validation_needs_the_expected_actions_all_passing(actions, statuses, passed):
    trace = [
        {"action": action, "status": status}
        for action, status in zip(actions, statuses, strict=True)
    ]
    trace.append({"action": "validate_assertions", "status": "pass"})

    result = docker_runner.process_validation(_browser_task(), trace)

    assert result == {
        "expected_actions": ["navigate"],
        "actual_actions": actions,
        "all_calls_passed": bool(actions) and statuses == ["pass"],
        "passed": passed,
    }


def test_tracks_keep_their_own_reset_scope(monkeypatch):
    cleared = {}
    monkeypatch.setattr(
        curated_runner, "reset_browser_state", lambda page, origins: cleared.update(last=origins)
    )
    monkeypatch.setattr(
        public_runner, "reset_browser_state", lambda page, origins: cleared.update(last=origins)
    )
    first = SimpleNamespace(url="https://first.test/a")
    second = SimpleNamespace(url="https://second.test/b")
    first.context = SimpleNamespace(pages=[first, second])

    curated_runner._reset_state(first, {"https://visited.test"})
    assert cleared["last"] == {"https://visited.test", "https://first.test"}

    public_runner._reset_state(first, {"https://visited.test"})
    assert cleared["last"] == {
        "https://visited.test",
        "https://first.test",
        "https://second.test",
    }


def test_tracks_keep_their_own_page_replacement(monkeypatch):
    for runner in (curated_runner, public_runner):
        monkeypatch.setattr(runner, "_reset_state", lambda *a: None)
    replacement = MagicMock()
    replacement.evaluate.return_value = {"width": 1280, "height": 900}
    current = MagicMock()
    other = MagicMock()
    current.context.pages = [current, other]
    current.context.new_page.return_value = replacement

    curated_runner._prepare_page(current, set(), settle_ms=0)
    current.close.assert_called_once()
    other.close.assert_not_called()

    public_runner._prepare_page(current, set(), settle_ms=0)
    other.close.assert_called_once()


def test_viewport_mismatch_stops_the_task_page():
    context = MagicMock()
    context.new_page.return_value.evaluate.return_value = {"width": 1, "height": 1}

    with pytest.raises(RuntimeError, match="browser viewport mismatch"):
        docker_runner.open_task_page(context, {"width": 1280, "height": 900}, settle_ms=0)


def test_failed_engine_start_keeps_started_resources_for_cleanup(monkeypatch):
    container = MagicMock()
    monkeypatch.setattr(docker_runner, "start_browser_container", lambda *a, **k: container)
    monkeypatch.setattr(docker_runner, "published_endpoint", lambda *a, **k: "http://test.invalid")

    def no_cdp(*args, **kwargs):
        raise TimeoutError("no CDP")

    monkeypatch.setattr(docker_runner, "wait_cdp", no_cdp)
    client = MagicMock()
    runtime = docker_runner.EngineRuntime()

    with pytest.raises(TimeoutError, match="no CDP"):
        docker_runner.start_engine(
            runtime,
            client,
            MagicMock(),
            engine="chromium",
            image="image",
            run_id="run",
            cpus=2.0,
            memory="2g",
            reset_state=lambda *a: None,
        )

    assert runtime.networks == {"chromium": client.networks.create.return_value}
    assert runtime.containers == {"chromium": container}
    assert runtime.browsers == {}


def _start_engine(runtime, playwright, reset_state, monkeypatch):
    start_container = MagicMock()
    client = MagicMock()
    monkeypatch.setattr(docker_runner, "start_browser_container", start_container)

    def published_endpoint(container, **kwargs):
        # The published port comes from container attributes that only a reload refreshes.
        container.reload.assert_called_once_with()
        return "http://test.invalid"

    monkeypatch.setattr(docker_runner, "published_endpoint", published_endpoint)
    monkeypatch.setattr(docker_runner, "wait_cdp", lambda *a, **k: "ws://test.invalid")
    monkeypatch.setattr(docker_runner, "DockerStatsResources", MagicMock())
    docker_runner.start_engine(
        runtime,
        client,
        playwright,
        engine="chromium",
        image="image",
        run_id="run",
        cpus=2.0,
        memory="2g",
        reset_state=reset_state,
    )
    return start_container, client


def test_engine_container_starts_on_its_own_bridge_with_the_requested_limits(monkeypatch):
    start_container, client = _start_engine(
        docker_runner.EngineRuntime(), MagicMock(), lambda *a: None, monkeypatch
    )

    client.networks.create.assert_called_once_with(
        "lex-browser-eval-run-chromium",
        driver="bridge",
        labels={"lex.browser-eval.run": "run", "lex.browser-eval.engine": "chromium"},
    )
    start_container.assert_called_once_with(
        client,
        image="image",
        cdp_port=docker_runner.ENGINE_PORTS["chromium"],
        engine="chromium",
        run_id="run",
        cpus=2.0,
        memory="2g",
        network=client.networks.create.return_value.name,
    )


def test_engine_page_is_published_only_after_the_warm_up_is_reset(monkeypatch):
    playwright = MagicMock()
    page = playwright.chromium.connect_over_cdp.return_value.contexts[0].pages[0]
    runtime = docker_runner.EngineRuntime()
    resets = []

    def reset_state(reset_page, origins):
        resets.append((reset_page, origins, dict(runtime.pages)))

    _start_engine(runtime, playwright, reset_state, monkeypatch)

    page.goto.assert_called_once()
    assert page.goto.call_args.args == ("https://example.com/",)
    assert resets == [(page, {"https://example.com"}, {})]
    assert runtime.pages == {"chromium": page}
    assert runtime.visited_origins == {"chromium": set()}


def test_failed_warm_up_reset_does_not_publish_the_engine_page(monkeypatch):
    runtime = docker_runner.EngineRuntime()

    def reset_state(page, origins):
        raise RuntimeError("reset failed")

    with pytest.raises(RuntimeError, match="reset failed"):
        _start_engine(runtime, MagicMock(), reset_state, monkeypatch)

    assert runtime.pages == {}


def _reset_page(*failing_methods: str, session_error: Exception | None = None) -> MagicMock:
    page = MagicMock()
    cdp = page.context.new_cdp_session.return_value
    if session_error is not None:
        page.context.new_cdp_session.side_effect = session_error

    def send(method, params=None):
        if method in failing_methods:
            raise RuntimeError(f"{method} failed")

    cdp.send.side_effect = send
    return page


def _sent(page: MagicMock) -> list[str]:
    cdp = page.context.new_cdp_session.return_value
    return [item.args[0] for item in cdp.send.call_args_list]


def _cleared_origins(page: MagicMock) -> list[str]:
    cdp = page.context.new_cdp_session.return_value
    return [
        item.args[1]["origin"]
        for item in cdp.send.call_args_list
        if item.args[0] == "Storage.clearDataForOrigin"
    ]


def _assert_cookies_cleared_and_page_blank(page: MagicMock) -> None:
    page.context.clear_cookies.assert_called_once_with()
    page.context.clear_permissions.assert_called_once_with()
    page.goto.assert_called_with("about:blank", wait_until="load", timeout=10_000)


def test_reset_clears_every_origin_once_in_sorted_order_and_leaves_a_blank_page():
    page = _reset_page()

    docker_runner.reset_browser_state(page, {"https://b.test", "https://a.test"})

    cdp = page.context.new_cdp_session.return_value
    cdp.send.assert_any_call(
        "Storage.clearDataForOrigin", {"origin": "https://a.test", "storageTypes": "all"}
    )
    cdp.send.assert_any_call(
        "Storage.clearDataForOrigin", {"origin": "https://b.test", "storageTypes": "all"}
    )
    assert _cleared_origins(page) == ["https://a.test", "https://b.test"]
    sent = _sent(page)
    assert sent.count("Network.clearBrowserCache") == 1
    assert sent.count("Network.clearBrowserCookies") == 1
    assert sent.count("Page.resetNavigationHistory") == 1
    page.evaluate.assert_called_once()
    page.goto.assert_called_once()
    _assert_cookies_cleared_and_page_blank(page)
    cdp.detach.assert_called_once_with()


def test_reset_continues_with_later_origins_when_clearing_one_fails():
    page = _reset_page()
    cdp = page.context.new_cdp_session.return_value

    def send(method, params=None):
        if method == "Storage.clearDataForOrigin" and params["origin"] == "https://a.test":
            raise RuntimeError("origin failed")

    cdp.send.side_effect = send

    docker_runner.reset_browser_state(page, {"https://b.test", "https://a.test"})

    assert _cleared_origins(page) == ["https://a.test", "https://b.test"]
    _assert_cookies_cleared_and_page_blank(page)
    cdp.detach.assert_called_once_with()


def test_reset_still_clears_context_state_when_page_storage_cannot_be_reached():
    page = _reset_page()
    page.evaluate.side_effect = RuntimeError("page crashed")

    docker_runner.reset_browser_state(page, {"https://a.test"})

    assert _cleared_origins(page) == ["https://a.test"]
    _assert_cookies_cleared_and_page_blank(page)


@pytest.mark.parametrize(
    "failing_method",
    ["Network.enable", "Network.clearBrowserCache", "Network.clearBrowserCookies"],
)
def test_reset_leaves_a_blank_page_when_a_cdp_clear_fails(failing_method):
    page = _reset_page(failing_method)

    docker_runner.reset_browser_state(page, {"https://a.test"})

    _assert_cookies_cleared_and_page_blank(page)


def test_reset_detaches_and_leaves_a_blank_page_when_history_reset_fails():
    page = _reset_page("Page.resetNavigationHistory")

    docker_runner.reset_browser_state(page, {"https://a.test"})

    assert _cleared_origins(page) == ["https://a.test"]
    _assert_cookies_cleared_and_page_blank(page)
    page.context.new_cdp_session.return_value.detach.assert_called_once_with()


def test_reset_leaves_a_blank_page_without_a_cdp_session():
    page = _reset_page(session_error=RuntimeError("no CDP session"))

    docker_runner.reset_browser_state(page, {"https://a.test"})

    _assert_cookies_cleared_and_page_blank(page)


@pytest.mark.parametrize(("attempt", "is_retry"), [(1, False), (2, True)])
def test_attempt_identity_opens_every_attempt_row(attempt, is_retry):
    task = _browser_task(tags=("retrieve", "form"))

    assert docker_runner.attempt_identity("moli", task, attempt, is_retry=is_retry) == {
        "engine": "moli",
        "task_id": "task",
        "title": "Task",
        "complexity": "simple",
        "tags": ["retrieve", "form"],
        "step_count": 1,
        "attempt": attempt,
        "is_retry": is_retry,
    }


def test_output_volume_counts_tokens_and_characters_across_outputs(monkeypatch):
    monkeypatch.setattr(docker_runner, "_encoding", lambda: SimpleNamespace(encode=str.split))

    assert docker_runner.output_volume({"first": "one two", "second": "三 四 五"}) == {
        "tool_output_tokens": 5,
        "output_chars": 12,
    }
    assert docker_runner.output_volume({}) == {"tool_output_tokens": 0, "output_chars": 0}


def test_manifest_task_records_the_frozen_task_definition():
    assertions = ({"output": "answer", "equals": "unique-73"},)
    task = _browser_task(assertions=assertions)

    assert docker_runner.manifest_task(task) == {
        "task_id": "task",
        "title": "Task",
        "step_count": 1,
        "complexity": "simple",
        "tags": ["retrieve"],
        "tool_calls": list(task.tool_calls),
        "assertions": list(assertions),
        "ground_truth": {"expected_actions": ["navigate"]},
        "source": {"kind": "test"},
    }


def test_ground_truth_validation_records_exactly_the_declared_evidence_fields():
    evidence = {
        "schema": "ground-truth/1",
        "dataset": "dataset.json",
        "dataset_sha256": "a" * 64,
        "source_commit": "b" * 40,
        "validator": "validate.py",
        "method": "independent",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "automatic_browser_replay": False,
        "human_review": {"reviewer": "maintainer"},
    }

    assert docker_runner.ground_truth_validation({**evidence, "private_log": "x"}) == evidence
    assert docker_runner.ground_truth_validation({"schema": "ground-truth/1"}) == {
        key: "ground-truth/1" if key == "schema" else None for key in evidence
    }


def test_ground_truth_validation_stays_absent_without_evidence():
    assert docker_runner.ground_truth_validation(None) is None


def test_body_text_truncates_to_the_limit_and_passes_the_timeout():
    page = MagicMock()
    inner_text = page.locator.return_value.first.inner_text
    inner_text.return_value = "abcdef"

    assert body_text(page, 250, 4) == "abcd"
    page.locator.assert_called_once_with("body")
    inner_text.assert_called_once_with(timeout=250)


def test_only_the_public_environment_records_the_image_build_source():
    container = MagicMock()
    container.exec_run.return_value = SimpleNamespace(output=b"version", exit_code=0)
    container.image.id = "sha256:image"
    container.image.tags = ["image:tag"]
    container.image.attrs = {
        "Config": {"Labels": {"lex.browser-eval.source": "commit", "other": "ignored"}}
    }
    client = MagicMock()
    client.info.return_value = {}
    client.version.return_value = {}

    shared = docker_runner.docker_environment(client, {"chromium": container})
    public = public_runner._environment(client, {"chromium": container})

    assert list(shared["images"]["chromium"]) == ["id", "tags"]
    assert list(public) == list(shared)
    assert public["images"]["chromium"] == {
        "id": "sha256:image",
        "tags": ["image:tag"],
        "build_source": {"lex.browser-eval.source": "commit"},
    }


def _environment_client() -> MagicMock:
    client = MagicMock()
    client.info.return_value = {
        "OperatingSystem": "Test OS",
        "Architecture": "x86_64",
        "NCPU": 8,
        "MemTotal": 1024,
        "CgroupVersion": "2",
    }
    client.version.return_value = {"Version": "27.0"}
    return client


def test_environment_receipt_records_the_host_docker_and_each_browser(monkeypatch):
    monkeypatch.setattr(docker_runner.platform, "platform", lambda: "test-platform")
    chromium = MagicMock()
    chromium.exec_run.return_value = SimpleNamespace(output=b" Chromium 120\n", exit_code=0)
    chromium.image.id = "sha256:chromium"
    chromium.image.tags = ["chromium:tag"]
    moli = MagicMock()
    moli.exec_run.return_value = SimpleNamespace(output=b"Moli 1\n", exit_code=0)
    moli.image.id = "sha256:moli"
    moli.image.tags = []

    environment = docker_runner.docker_environment(
        _environment_client(), {"chromium": chromium, "moli": moli}
    )

    assert environment == {
        "runner_platform": "test-platform",
        "docker_server_version": "27.0",
        "docker_os": "Test OS",
        "docker_architecture": "x86_64",
        "docker_cpus": 8,
        "docker_memory_bytes": 1024,
        "cgroup_version": "2",
        "browser_versions": {"chromium": "Chromium 120", "moli": "Moli 1"},
        "images": {
            "chromium": {"id": "sha256:chromium", "tags": ["chromium:tag"]},
            "moli": {"id": "sha256:moli", "tags": []},
        },
    }
    chromium.exec_run.assert_called_once_with(["chromium", "--version"])
    moli.exec_run.assert_called_once_with(["moli", "--version"])


@pytest.mark.parametrize(
    ("exec_result", "recorded"),
    [
        (SimpleNamespace(output=b" \n", exit_code=7), "exit 7, no output"),
        (SimpleNamespace(output=b"\xff", exit_code=0), "\ufffd"),
        (RuntimeError("exec failed"), "unavailable: RuntimeError"),
    ],
)
def test_environment_receipt_keeps_a_browser_version_it_could_not_read(exec_result, recorded):
    container = MagicMock()
    container.exec_run.side_effect = [exec_result]

    environment = docker_runner.docker_environment(_environment_client(), {"moli": container})

    assert environment["browser_versions"] == {"moli": recorded}


def test_run_artifacts_keep_non_ascii_text_and_hash_the_written_bytes(tmp_path):
    rows = [{"title": "\u4efb\u52a1 1"}, {"title": "caf\u00e9"}]
    summary = {"title": "\u6c47\u603b"}
    manifest = {"title": "\u6e05\u5355"}

    docker_runner.write_run_artifacts(tmp_path, rows, summary, manifest)

    attempts = (tmp_path / "attempts.jsonl").read_bytes()
    written_summary = (tmp_path / "summary.json").read_bytes()
    assert attempts == '{"title": "\u4efb\u52a1 1"}\n{"title": "caf\u00e9"}\n'.encode()
    assert written_summary == '{\n  "title": "\u6c47\u603b"\n}\n'.encode()
    provenance = {
        "artifact_directory": str(tmp_path),
        "attempts_sha256": hashlib.sha256(attempts).hexdigest(),
        "summary_sha256": hashlib.sha256(written_summary).hexdigest(),
    }
    assert manifest["artifact_provenance"] == provenance
    expected_manifest = json.dumps(
        {"title": "\u6e05\u5355", "artifact_provenance": provenance},
        indent=2,
        ensure_ascii=False,
    )
    assert (tmp_path / "manifest.json").read_bytes() == f"{expected_manifest}\n".encode()
