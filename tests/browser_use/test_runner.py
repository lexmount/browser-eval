import pytest
from webarena_verified.types.task import WebArenaSite

from harness.browser_use.runner import (
    ENV_CTRL_READY_TIMEOUT_SECONDS,
    QEMU_X86_64_HOST_PATH,
    SITE_AUTH_HEADERS,
    _allow_official_container_hostname,
    _configure_benchmark,
    _container_host_gateway,
    _limit_cowagent_transport_retries,
    _login_context,
    _scheduled_tasks,
    _site_container_manager,
    _site_runtime_compatibility,
    _user_prompt,
    _wait_application_ready,
    _wait_container_healthy,
)
from harness.browser_use.selection import load_pilot


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


def test_user_prompt_exposes_schema_but_not_reference_answer() -> None:
    benchmark = _configure_benchmark()
    task = benchmark.get_task(41)
    prompt = _user_prompt(task, ["http://host.docker.internal:7780/admin"])

    assert "task_type: RETRIEVE" in prompt
    assert '"type":"array"' in prompt
    assert "hollister" not in prompt


def test_max_tasks_limits_only_the_current_invocation() -> None:
    tasks = load_pilot("evaluation/browser-use/webarena-verified-pilot.json").tasks

    assert _scheduled_tasks(tasks, None) == tasks
    assert _scheduled_tasks(tasks, 1) == tasks[:1]
    with pytest.raises(ValueError, match="between 1 and 30"):
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

    _login_context(Service(), (), object(), ["http://official.test/start"])

    assert stale.closed is True
    assert primary.url == "http://official.test/start"


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
