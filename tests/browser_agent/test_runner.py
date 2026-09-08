import pytest
from webarena_verified.types.task import WebArenaSite

from harness.browser_agent.runner import (
    SITE_AUTH_HEADERS,
    _configure_benchmark,
    _login_context,
    _scheduled_tasks,
    _user_prompt,
)
from harness.browser_agent.selection import load_pilot


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
    tasks = load_pilot("evaluation/browser-agent/webarena-verified-pilot.json").tasks

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
