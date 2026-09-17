import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.browser_use.runner import (
    ENV_CTRL_READY_TIMEOUT_SECONDS,
    MAGENTO_DEPENDENCY_READY_TIMEOUT_SECONDS,
    PHYSICAL_EXECUTION_LOCK_NAMESPACE,
    QEMU_X86_64_HOST_PATH,
    SITE_AUTH_HEADERS,
    WIRE_ACCESS_LOG_PATHS,
    _allow_official_container_hostname,
    _configure_benchmark,
    _container_host_gateway,
    _docker_daemon_lock_namespace,
    _environment_prepare_receipt,
    _exhausted_agent_steps,
    _limit_cowagent_transport_retries,
    _load_completed_attempts,
    _login_context,
    _max_steps_response,
    _physical_execution_lock,
    _physical_execution_lock_path,
    _preserve_aborted_attempt,
    _reconcile_server_wire_headers,
    _run_attempt,
    _scheduled_tasks,
    _site_container_manager,
    _site_runtime_compatibility,
    _user_prompt,
    _wait_application_ready,
    _wait_container_healthy,
    _wait_magento_dependencies_ready,
    _wire_log_cursors,
)
from harness.browser_use.selection import load_pilot


def _hold_physical_execution_lock(lock_path, acquired, release) -> None:
    with _physical_execution_lock(Path(lock_path)):
        acquired.set()
        release.wait(timeout=5)


def test_official_container_urls_and_header_auth() -> None:
    benchmark = _configure_benchmark()

    assert (
        benchmark.config.get_environment(WebArenaSite.SHOPPING_ADMIN).active_url
        == "http://host.docker.internal:7780/admin"
    )
    assert SITE_AUTH_HEADERS == {
        WebArenaSite.SHOPPING: "X-M2-Customer-Auto-Login",
        WebArenaSite.SHOPPING_ADMIN: "X-M2-Admin-Auto-Login",
        WebArenaSite.REDDIT: "X-Postmill-Auto-Login",
    }


def test_wire_witness_reads_only_post_setup_bytes_and_binds_separate_hars(
    monkeypatch, tmp_path: Path
) -> None:
    initial = b'prior log line\n'
    new_line = (
        b'127.0.0.1 - - [17/Sep/2026:00:00:00 +0000] '
        b'"POST /f/space/subscribe.json HTTP/1.1" 200 19 '
        b'"http://site.test/f/space/thread" "agent"\n'
    )
    state = {"log": initial}

    class Container:
        name = "site"
        image = SimpleNamespace(id="sha256:site")

        def exec_run(self, command):
            if command[0] == "stat":
                return SimpleNamespace(exit_code=0, output=str(len(state["log"])).encode())
            if command[0] == "tail":
                offset = int(command[2][1:]) - 1
                return SimpleNamespace(exit_code=0, output=state["log"][offset:])
            raise AssertionError(command)

    container = Container()
    monkeypatch.setattr(
        "harness.browser_use.runner.docker.from_env",
        lambda: SimpleNamespace(containers=SimpleNamespace(get=lambda name: container)),
    )
    monkeypatch.setattr(
        "harness.browser_use.runner._site_container_manager",
        lambda site: SimpleNamespace(container_name="site"),
    )
    benchmark = SimpleNamespace(
        config=SimpleNamespace(
            get_environment=lambda site: SimpleNamespace(active_url="http://site.test")
        )
    )
    cursors = _wire_log_cursors((WebArenaSite.REDDIT,), benchmark)
    assert cursors[0]["offset"] == len(initial)
    state["log"] += new_line

    raw = tmp_path / "network-cdp.har"
    evaluated = tmp_path / "network.har"
    witness = tmp_path / "network-wire-witness.json"
    source = tmp_path / "network-wire-source.zip"
    raw.write_text(json.dumps({"log": {"entries": [{
        "request": {
            "method": "POST",
            "url": "http://site.test/f/space/subscribe.json",
            "headers": [],
        },
        "response": {"status": 200},
    }]}}))
    receipt = _reconcile_server_wire_headers(raw, evaluated, witness, source, cursors)
    headers = json.loads(evaluated.read_text())["log"]["entries"][0]["request"]["headers"]
    assert headers == [{"name": "Referer", "value": "http://site.test/f/space/thread"}]
    assert json.loads(raw.read_text())["log"]["entries"][0]["request"]["headers"] == []
    assert len(receipt["unique_header_additions"]) == 1
    assert "thread" not in witness.read_text()
    assert source.is_file()


def test_missing_server_log_fails_closed(monkeypatch) -> None:
    assert "/var/log/gitlab/nginx/gitlab_access.log" in WIRE_ACCESS_LOG_PATHS
    container = SimpleNamespace(
        name="site",
        image=SimpleNamespace(id="sha256:site"),
        exec_run=lambda command: SimpleNamespace(exit_code=1, output=b""),
    )
    monkeypatch.setattr(
        "harness.browser_use.runner.docker.from_env",
        lambda: SimpleNamespace(containers=SimpleNamespace(get=lambda name: container)),
    )
    monkeypatch.setattr(
        "harness.browser_use.runner._site_container_manager",
        lambda site: SimpleNamespace(container_name="site"),
    )
    with pytest.raises(RuntimeError, match="no readable server access log for gitlab"):
        _wire_log_cursors((WebArenaSite.GITLAB,), SimpleNamespace())


def test_aborted_attempt_is_preserved_before_retry(tmp_path: Path) -> None:
    attempt = tmp_path / "chromium" / "44" / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "network-cdp.har").write_text("raw")
    saved = _preserve_aborted_attempt(tmp_path, attempt)
    assert saved is not None
    assert (saved / "network-cdp.har").read_text() == "raw"
    assert not attempt.exists()
    assert _preserve_aborted_attempt(tmp_path, attempt) is None


def test_user_prompt_exposes_schema_but_not_reference_answer() -> None:
    benchmark = _configure_benchmark()
    task = benchmark.get_task(41)
    prompt = _user_prompt(task, ["http://host.docker.internal:7780/admin"])

    assert "task_type: RETRIEVE" in prompt
    assert '"type":"array"' in prompt
    assert "hollister" not in prompt


def test_system_contract_fails_empty_retrieve_as_not_found() -> None:
    from harness.browser_use.runner import _system_prompt

    prompt = _system_prompt()
    assert "NOT_FOUND_ERROR with retrieved_data null" in prompt
    assert "search and list pages as candidate sets" in prompt
    assert "retrieved_data is always a list" not in prompt


def test_agent_step_exhaustion_requires_a_final_tool_call() -> None:
    exhausted = [
        {"type": "turn_end", "data": {"turn": turn, "has_tool_calls": True}}
        for turn in range(1, 41)
    ]
    zero_indexed = [
        {"type": "turn_end", "data": {"turn": turn, "has_tool_calls": True}} for turn in range(40)
    ]
    completed = [
        {
            "type": "turn_end",
            "data": {"turn": turn, "has_tool_calls": turn != 40},
        }
        for turn in range(1, 41)
    ]

    assert _exhausted_agent_steps(exhausted, 40) is True
    assert _exhausted_agent_steps(zero_indexed, 40) is True
    assert _exhausted_agent_steps(completed, 40) is False
    assert _max_steps_response("retrieve", 40) == {
        "task_type": "RETRIEVE",
        "status": "UNKNOWN_ERROR",
        "retrieved_data": None,
        "error_details": (
            "Agent reached the 40-step decision limit before producing "
            "a tool-free, post-action completion."
        ),
    }


def test_max_tasks_limits_only_the_current_invocation() -> None:
    tasks = load_pilot("evaluation/browser-use/webarena-verified-pilot.json").tasks

    assert _scheduled_tasks(tasks, None) == tasks
    assert _scheduled_tasks(tasks, 1) == tasks[:1]
    with pytest.raises(ValueError, match="between 1 and 90"):
        _scheduled_tasks(tasks, 0)


def test_login_context_closes_old_tabs_and_opens_official_start_url() -> None:
    class Page:
        def __init__(self) -> None:
            self.closed = False
            self.url = "about:blank"

        def close(self) -> None:
            self.closed = True

        def set_default_timeout(self, _timeout: int) -> None:
            pass

        def set_default_navigation_timeout(self, _timeout: int) -> None:
            pass

        def goto(self, url: str, **_kwargs) -> None:
            self.url = url

        def wait_for_timeout(self, _timeout: int) -> None:
            pass

    primary = Page()
    stale = Page()

    class Context:
        pages = [primary, stale]

        def clear_cookies(self) -> None:
            pass

        def set_extra_http_headers(self, _headers) -> None:
            pass

    class Service:
        _context = Context()
        _page = primary

        def _submit(self, callback):
            return callback()

    readiness_retries = _login_context(Service(), (), object(), ["http://official.test/start"])

    assert readiness_retries == 0
    assert stale.closed is True
    assert primary.url == "http://official.test/start"


def test_login_context_recovers_one_transient_unloaded_context(monkeypatch) -> None:
    calls = 0

    class Service:
        def _submit(self, callback):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Protocol error: BrowserContextNotLoaded")
            return callback()

        class Context:
            pages = []

            @staticmethod
            def clear_cookies() -> None:
                pass

            @staticmethod
            def set_extra_http_headers(_headers) -> None:
                pass

        class Page:
            @staticmethod
            def set_default_timeout(_timeout: int) -> None:
                pass

            @staticmethod
            def set_default_navigation_timeout(_timeout: int) -> None:
                pass

            @staticmethod
            def goto(_url: str, **_kwargs) -> None:
                pass

            @staticmethod
            def wait_for_timeout(_timeout: int) -> None:
                pass

        _context = Context()
        _page = Page()

    monkeypatch.setattr("harness.browser_use.runner.time.sleep", lambda _seconds: None)

    assert _login_context(Service(), (), object(), ["http://official.test/start"]) == 1
    assert calls == 2


def test_login_context_does_not_retry_other_setup_errors(monkeypatch) -> None:
    calls = 0

    class Service:
        def _submit(self, _callback):
            nonlocal calls
            calls += 1
            raise RuntimeError("authentication failed")

    monkeypatch.setattr("harness.browser_use.runner.time.sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="authentication failed"):
        _login_context(Service(), (), object(), ["http://official.test/start"])
    assert calls == 1


def test_container_host_gateway_returns_literal_ip() -> None:
    class Result:
        exit_code = 0
        output = b"192.168.5.2 host.docker.internal\n"

    class Container:
        def exec_run(self, command):
            assert command == ["getent", "hosts", "host.docker.internal"]
            return Result()

    assert _container_host_gateway(Container()) == "192.168.5.2"


def test_wait_container_healthy_waits_past_starting(monkeypatch) -> None:
    class Container:
        attrs = {"State": {"Health": {"Status": "starting"}}}

        def reload(self):
            self.attrs = {"State": {"Health": {"Status": "healthy"}}}

    monkeypatch.setattr("harness.browser_use.runner.time.sleep", lambda _seconds: None)
    _wait_container_healthy(Container(), timeout_seconds=1)


def test_application_readiness_does_not_follow_container_only_hostname(monkeypatch) -> None:
    calls = []

    class Response:
        status_code = 302

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("harness.browser_use.runner.requests.get", get)
    _wait_application_ready(
        WebArenaSite.SHOPPING_ADMIN,
        "/",
        timeout_seconds=1,
        stable_responses=1,
    )

    assert calls == [
        (
            "http://127.0.0.1:7780/",
            {"timeout": 15, "allow_redirects": False},
        )
    ]


def test_environment_control_wait_covers_slow_official_image_restore() -> None:
    assert ENV_CTRL_READY_TIMEOUT_SECONDS == 600
    assert MAGENTO_DEPENDENCY_READY_TIMEOUT_SECONDS == 300


def test_magento_readiness_requires_non_red_elasticsearch(monkeypatch) -> None:
    class Result:
        def __init__(self, exit_code: int | None, output: bytes) -> None:
            self.exit_code = exit_code
            self.output = output

    class Container:
        def __init__(self) -> None:
            self.calls = 0

        def exec_run(self, command):
            self.calls += 1
            assert command[-1].endswith("wait_for_status=yellow&timeout=5s")
            if self.calls == 1:
                return Result(None, b"")
            if self.calls == 2:
                return Result(7, b"connection refused")
            return Result(0, b'{"status":"yellow"}')

    monkeypatch.setattr("harness.browser_use.runner.time.sleep", lambda _: None)
    container = Container()
    _wait_magento_dependencies_ready(container, timeout_seconds=1)
    assert container.calls == 3


def test_physical_execution_lock_path_is_user_global_not_evidence_scoped(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("harness.browser_use.runner.tempfile.gettempdir", lambda: "/tmp")
    monkeypatch.setattr("harness.browser_use.runner.os.getuid", lambda: 1234)

    assert _physical_execution_lock_path() == (
        Path("/tmp")
        / "browser-eval-1234"
        / "locks"
        / f"{PHYSICAL_EXECUTION_LOCK_NAMESPACE}.lock"
    )


def test_environment_lane_is_bound_to_the_docker_daemon() -> None:
    first = _docker_daemon_lock_namespace({"ID": "daemon-a"})
    second = _docker_daemon_lock_namespace({"ID": "daemon-b"})

    assert first.startswith(f"{PHYSICAL_EXECUTION_LOCK_NAMESPACE}-")
    assert first != second
    assert _docker_daemon_lock_namespace({"ID": "daemon-a"}) == first
    assert _physical_execution_lock_path(first).name == f"{first}.lock"


def test_environment_lane_fails_closed_without_a_docker_daemon_id() -> None:
    with pytest.raises(RuntimeError, match="Docker daemon ID"):
        _docker_daemon_lock_namespace({})


def test_prepare_receipt_records_lock_wait_and_namespace_without_path() -> None:
    receipt = _environment_prepare_receipt(
        (WebArenaSite.REDDIT,),
        lock_wait_ms=12.3456,
        lock_namespace=PHYSICAL_EXECUTION_LOCK_NAMESPACE,
    )

    assert receipt["physical_execution_lock"] == {
        "namespace": PHYSICAL_EXECUTION_LOCK_NAMESPACE,
        "wait_ms": 12.346,
    }
    assert "path" not in receipt["physical_execution_lock"]


def test_physical_execution_lock_serializes_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    lock_path = tmp_path / "physical-execution.lock"
    first_acquired = context.Event()
    first_release = context.Event()
    second_acquired = context.Event()
    second_release = context.Event()
    first = context.Process(
        target=_hold_physical_execution_lock,
        args=(str(lock_path), first_acquired, first_release),
    )
    second = context.Process(
        target=_hold_physical_execution_lock,
        args=(str(lock_path), second_acquired, second_release),
    )
    try:
        first.start()
        assert first_acquired.wait(timeout=2)
        second.start()
        assert not second_acquired.wait(timeout=0.2)
        first_release.set()
        assert second_acquired.wait(timeout=2)
    finally:
        first_release.set()
        second_release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        if first.is_alive():
            first.terminate()
        if second.is_alive():
            second.terminate()


def test_run_attempt_holds_lock_around_complete_physical_execution(
    monkeypatch, tmp_path: Path
) -> None:
    context = multiprocessing.get_context("fork")
    lock_path = tmp_path / "physical-execution.lock"
    competing_acquired = context.Event()
    competing_release = context.Event()
    competing = context.Process(
        target=_hold_physical_execution_lock,
        args=(str(lock_path), competing_acquired, competing_release),
    )

    def run_locked(**kwargs):
        assert kwargs["lock_namespace"] == PHYSICAL_EXECUTION_LOCK_NAMESPACE
        assert kwargs["lock_wait_ms"] >= 0
        competing.start()
        assert not competing_acquired.wait(timeout=0.2)
        return {"status": "complete"}

    monkeypatch.setattr("harness.browser_use.runner._run_attempt_locked", run_locked)
    try:
        assert _run_attempt(lock_path=lock_path) == {"status": "complete"}
        assert competing_acquired.wait(timeout=2)
    finally:
        competing_release.set()
        competing.join(timeout=2)
        if competing.is_alive():
            competing.terminate()


def test_run_attempt_releases_lock_after_exception(monkeypatch, tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    lock_path = tmp_path / "physical-execution.lock"

    def fail_locked(**_kwargs):
        raise RuntimeError("attempt failed")

    monkeypatch.setattr("harness.browser_use.runner._run_attempt_locked", fail_locked)
    with pytest.raises(RuntimeError, match="attempt failed"):
        _run_attempt(lock_path=lock_path)

    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_physical_execution_lock,
        args=(str(lock_path), acquired, release),
    )
    try:
        process.start()
        assert acquired.wait(timeout=2)
    finally:
        release.set()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()


def test_arm_php_sites_mount_targeted_qemu_compatibility(monkeypatch) -> None:
    class Client:
        @staticmethod
        def info():
            return {"Architecture": "aarch64"}

    monkeypatch.setattr("harness.browser_use.runner.docker.from_env", lambda: Client())
    manager = _site_container_manager(WebArenaSite.SHOPPING)

    assert manager.config.docker_img == "am1n3e/webarena-verified-shopping"
    assert manager.config.volumes["/usr/bin/qemu-x86_64"] == "/usr/local/bin/qemu-x86_64"
    assert manager.config.volumes[str(manager.config.setup.volumes[1].volume_name)].endswith(
        "/etc/supervisor.d/php-fpm.ini:ro"
    )


def test_non_php_site_keeps_official_default_config(monkeypatch) -> None:
    class Client:
        @staticmethod
        def info():
            return {"Architecture": "aarch64"}

    monkeypatch.setattr("harness.browser_use.runner.docker.from_env", lambda: Client())
    manager = _site_container_manager(WebArenaSite.GITLAB)

    assert manager.config.docker_img == "am1n3e/webarena-verified-gitlab"
    assert manager.config.volumes == {}


def test_php_site_on_x86_keeps_official_default_config(monkeypatch) -> None:
    class Client:
        @staticmethod
        def info():
            return {"Architecture": "x86_64"}

    monkeypatch.setattr("harness.browser_use.runner.docker.from_env", lambda: Client())
    manager = _site_container_manager(WebArenaSite.SHOPPING)

    assert QEMU_X86_64_HOST_PATH not in manager.config.volumes


def test_arm_runtime_compatibility_is_evidence_bound() -> None:
    compatibility = _site_runtime_compatibility("aarch64")

    assert compatibility["mode"] == "rosetta-with-qemu-php-fpm"
    assert compatibility["sites"] == ["reddit", "shopping", "shopping_admin"]
    assert len(compatibility["supervisor_config_sha256"]) == 64


def test_cowagent_transport_retry_limit_is_exactly_one() -> None:
    calls = []

    class Executor:
        def _call_llm_stream(
            self, retry_on_empty=True, retry_count=0, max_retries=3, _overflow_stage=0
        ):
            calls.append(max_retries)
            return "ok"

    _limit_cowagent_transport_retries(Executor)

    assert Executor()._call_llm_stream(max_retries=9) == "ok"
    assert calls == [1]


def test_only_official_container_hostname_bypasses_host_dns_validation() -> None:
    checked = []

    class Tool:
        @staticmethod
        def _validate_url_safe(url):
            checked.append(url)

    tool = Tool()
    _allow_official_container_hostname(tool)

    tool._validate_url_safe("http://host.docker.internal:7780/admin")
    tool._validate_url_safe("https://example.com")
    assert checked == ["https://example.com"]


def test_resume_key_includes_and_validates_model(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        json.dumps({"engine": "chromium", "task_id": 44, "attempt": 1}) + "\n",
        encoding="utf-8",
    )

    completed = _load_completed_attempts(attempts, "model-a")

    assert set(completed) == {("model-a", "chromium", 44, 1)}
    assert completed[("model-a", "chromium", 44, 1)]["model"] == "model-a"


def test_resume_rejects_attempt_from_another_model(tmp_path: Path) -> None:
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        json.dumps({"model": "model-b", "engine": "chromium", "task_id": 44, "attempt": 1}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="attempt model differs"):
        _load_completed_attempts(attempts, "model-a")
