"""Exercise the CowAgent-free path against a real Chromium page."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness.browser_use.runner import _prepare_browser_viewport
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.remote_execution import _browser_command, _wait_cdp
from harness.trajectory_replay.webarena_replay import _execute_replay_call


def _unit_tool(page, context, *, engine="chromium"):
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    tool = PlaywrightReplayTool.__new__(PlaywrightReplayTool)
    tool._page = page
    tool._context = context
    tool._engine = engine
    return tool


def test_obscura_viewport_verification_does_not_open_a_second_page(monkeypatch) -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    class Page:
        def evaluate(self, _script):
            raise AssertionError("Obscura viewport verification must not execute page script")

    page = Page()

    class Context:
        def __init__(self):
            self.page_count = 0

        def new_page(self):
            self.page_count += 1
            if self.page_count > 1:
                raise AssertionError("Obscura viewport verification must reuse the primary page")
            return page

        def close(self):
            pass

        def new_cdp_session(self, selected):
            assert selected is page

            class Session:
                def send(self, method, params=None):
                    if method == "Page.getLayoutMetrics":
                        return {
                            "cssVisualViewport": {
                                "clientWidth": 1280,
                                "clientHeight": 900,
                            }
                        }
                    assert method == "Runtime.evaluate"
                    assert params == {
                        "expression": "devicePixelRatio",
                        "returnByValue": True,
                    }
                    return {"result": {"value": 1}}

            return Session()

    context = Context()

    class Browser:
        contexts = []

        def new_context(self, **kwargs):
            assert kwargs == {"viewport": {"width": 1280, "height": 900}, "locale": "en-US"}
            return context

        def close(self):
            pass

    class Playwright:
        class Chromium:
            def connect_over_cdp(self, *_args, **_kwargs):
                return Browser()

        chromium = Chromium()

        def stop(self):
            pass

    class Starter:
        def start(self):
            return Playwright()

    monkeypatch.setattr(
        "harness.trajectory_replay.playwright_tool.sync_playwright", lambda: Starter()
    )

    tool = PlaywrightReplayTool("http://127.0.0.1:1", engine="obscura")
    try:
        viewport = _prepare_browser_viewport(tool)
    finally:
        tool.close()

    assert context.page_count == 1
    assert viewport["page_model"] == "single_target"
    assert viewport["verified"] is True
    assert viewport["observations"][0]["device_scale_factor"] == 1


def test_kitesurf_reuses_authenticated_default_target_without_second_cdp_attach(
    monkeypatch,
) -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    events = []

    class Page:
        url = "about:blank"

        def on(self, event, handler):
            self.handlers = getattr(self, "handlers", {})
            self.handlers[event] = handler

        def set_viewport_size(self, viewport):
            events.append(("viewport", viewport))

        def goto(self, url, **kwargs):
            events.append(("goto", url, kwargs))

        def evaluate(self, _script):
            return {"width": 1280, "height": 900, "device_scale_factor": 1}

    page = Page()

    class Context:
        pages = [page]

        def route(self, selector, handler):
            events.append(("route", selector, handler))

        def new_cdp_session(self, _page):
            raise AssertionError("Kitesurf must not attach a second CDP session")

        def close(self):
            raise AssertionError("Kitesurf default context must not be closed separately")

    context = Context()

    class Browser:
        contexts = [context]

        def new_browser_cdp_session(self):
            class Session:
                def send(self, method):
                    events.append(("browser_cdp", method))
                    return {
                        "protocolVersion": "1.3",
                        "product": "Chrome/145.0.0.0",
                        "revision": "@kitesurf",
                        "userAgent": "Kitesurf",
                        "jsVersion": "14.5",
                    }

                def detach(self):
                    events.append(("browser_cdp", "detach"))

            return Session()

        def new_context(self, **_kwargs):
            raise AssertionError("Kitesurf must reuse its provider-owned default context")

        def close(self):
            events.append(("browser", "close"))

    class Playwright:
        class Chromium:
            def connect_over_cdp(self, endpoint, **kwargs):
                events.append(("connect", endpoint, kwargs))
                return Browser()

        chromium = Chromium()

        def stop(self):
            events.append(("playwright", "stop"))

    class Starter:
        def start(self):
            return Playwright()

    monkeypatch.setattr(
        "harness.trajectory_replay.playwright_tool.sync_playwright", lambda: Starter()
    )

    class BridgeResponse:
        status_code = 201
        headers = {"content-type": "application/json", "content-length": "14"}
        content = b'{"saved":true}'
        url = "http://private.test/save"
        cookies = []

    class BridgeSession:
        trust_env = True

        def __init__(self):
            self.calls = 0
            self.cookies = []

        def request(self, **kwargs):
            self.calls += 1
            events.append(("bridge", "request", kwargs))
            if self.calls == 2:
                raise RuntimeError("local fetch failed")
            return BridgeResponse()

        def close(self):
            events.append(("bridge", "close"))

    monkeypatch.setattr(
        "harness.trajectory_replay.playwright_tool.requests.Session", BridgeSession
    )
    tool = PlaywrightReplayTool(
        "wss://provider.example/cdp",
        engine="kitesurf",
        headers={"Authorization": "Bearer secret"},
        request_bridge_origins={"http://private.test"},
    )
    assert tool.har_capture_transport == "playwright"
    assert tool.runtime_metadata == {
        "protocolVersion": "1.3",
        "product": "Chrome/145.0.0.0",
        "revision": "@kitesurf",
        "userAgent": "Kitesurf",
        "jsVersion": "14.5",
    }
    assert tool._supports_multiple_pages is False
    assert tool._supports_network_reset_cdp is False
    assert tool._supports_storage_cdp is False
    assert tool._native_login_form_submit is True
    route_event = next(event for event in events if event[0] == "route")
    predicate, handler = route_event[1:]
    assert predicate("http://private.test/path") is True
    assert predicate("https://public.test/path") is False

    class Request:
        method = "POST"
        url = "http://private.test/save"
        post_data_buffer = b'{"enabled":true}'
        resource_type = "xhr"

        def all_headers(self):
            return {
                "content-type": "application/json",
                "content-length": "16",
                "signature": "private-signature",
            }

    class Route:
        request = Request()

        def fulfill(self, **kwargs):
            events.append(("bridge", "fulfill", kwargs))

    handler(Route())
    assert tool.request_bridge_stats == {"requests": 1, "failures": 0}

    class FailedRoute:
        request = Request()

        def abort(self, reason):
            events.append(("bridge", "abort", reason))

    with pytest.raises(RuntimeError, match="local fetch failed"):
        handler(FailedRoute())
    assert tool.request_bridge_stats == {"requests": 2, "failures": 1}
    tool.close()

    assert events == [
        (
            "connect",
            "wss://provider.example/cdp",
            {"timeout": 90_000, "headers": {"Authorization": "Bearer secret"}},
        ),
        ("browser_cdp", "Browser.getVersion"),
        ("browser_cdp", "detach"),
        ("route", predicate, handler),
        ("viewport", {"width": 1280, "height": 900}),
        ("goto", "about:blank", {"wait_until": "load", "timeout": 10_000}),
        (
            "bridge",
            "request",
            {
                "method": "POST",
                "url": "http://private.test/save",
                "headers": {"content-type": "application/json"},
                "data": b'{"enabled":true}',
                "allow_redirects": False,
                "timeout": 60,
            },
        ),
        (
            "bridge",
            "fulfill",
            {
                "status": 201,
                "headers": {"content-type": "application/json"},
                "body": b'{"saved":true}',
            },
        ),
        (
            "bridge",
            "request",
            {
                "method": "POST",
                "url": "http://private.test/save",
                "headers": {"content-type": "application/json"},
                "data": b'{"enabled":true}',
                "allow_redirects": False,
                "timeout": 60,
            },
        ),
        ("bridge", "abort", "failed"),
        ("bridge", "close"),
        ("browser", "close"),
        ("playwright", "stop"),
    ]


def test_kitesurf_login_form_is_submitted_by_local_bridge() -> None:
    events = []

    class Submitter:
        def evaluate(self, script):
            assert "new FormData(form)" in script
            return {
                "action": "http://gitlab.test/users/sign_in",
                "method": "post",
                "fields": [
                    ["user[login]", "alice"],
                    ["user[password]", "secret"],
                    ["authenticity_token", "csrf"],
                ],
            }

    class RedirectResponse:
        status_code = 302
        url = "http://gitlab.test/users/sign_in"
        headers = {"location": "/"}
        cookies = []

    class FinalResponse:
        status_code = 200
        url = "http://gitlab.test/"
        headers = {}
        cookies = []

    class Session:
        responses = iter((RedirectResponse(), FinalResponse()))

        def request(self, **kwargs):
            events.append(("request", kwargs))
            return next(self.responses)

    class Page:
        url = "http://gitlab.test/users/sign_in"

        def goto(self, url, **kwargs):
            events.append(("goto", url, kwargs))

    tool = _unit_tool(Page(), object(), engine="kitesurf")
    tool._request_bridge_session = Session()
    tool._sync_request_bridge_cookies = lambda url, cookies: events.append(
        ("cookies", url, list(cookies))
    )

    final_url = tool._submit_login_form(tool._page, Submitter())

    assert final_url == "http://gitlab.test/"
    assert events == [
        (
            "request",
            {
                "method": "post",
                "url": "http://gitlab.test/users/sign_in",
                "headers": {"referer": "http://gitlab.test/users/sign_in"},
                "allow_redirects": False,
                "timeout": 60,
                "data": [
                    ["user[login]", "alice"],
                    ["user[password]", "secret"],
                    ["authenticity_token", "csrf"],
                ],
            },
        ),
        ("cookies", "http://gitlab.test/users/sign_in", []),
        (
            "request",
            {
                "method": "get",
                "url": "http://gitlab.test/",
                "headers": {"referer": "http://gitlab.test/users/sign_in"},
                "allow_redirects": False,
                "timeout": 60,
            },
        ),
        ("cookies", "http://gitlab.test/", []),
        (
            "goto",
            "http://gitlab.test/",
            {"wait_until": "domcontentloaded", "timeout": 90_000},
        ),
    ]


def test_request_bridge_cookie_sync_preserves_host_only_scope() -> None:
    added = []
    context = type("Context", (), {"add_cookies": lambda self, rows: added.extend(rows)})()
    tool = _unit_tool(object(), context, engine="kitesurf")
    tool._request_bridge_origins = frozenset({"http://shop.test"})
    cookie = type(
        "Cookie",
        (),
        {
            "name": "session",
            "value": "one",
            "path": "/",
            "secure": False,
            "expires": None,
            "domain": "shop.test",
            "domain_specified": False,
            "_rest": {"HttpOnly": None, "SameSite": "Lax"},
        },
    )()

    tool._sync_request_bridge_cookies("http://shop.test/account", [cookie])

    assert added == [
        {
            "name": "session",
            "value": "one",
            "url": "http://shop.test/",
            "secure": False,
            "httpOnly": True,
            "sameSite": "Lax",
        }
    ]


def test_request_bridge_cookie_sync_rejects_nonroot_host_only_path() -> None:
    tool = _unit_tool(
        object(), type("Context", (), {"add_cookies": lambda *_args: None})(), engine="kitesurf"
    )
    tool._request_bridge_origins = frozenset({"http://shop.test"})
    cookie = type(
        "Cookie",
        (),
        {
            "name": "session",
            "value": "one",
            "path": "/account",
            "secure": False,
            "expires": None,
            "domain": "shop.test",
            "domain_specified": False,
            "_rest": {},
        },
    )()

    with pytest.raises(RuntimeError, match="non-root host-only cookie path"):
        tool._sync_request_bridge_cookies("http://shop.test/account", [cookie])


def test_request_bridge_cookie_sync_preserves_explicit_domain_scope() -> None:
    added = []
    context = type("Context", (), {"add_cookies": lambda self, rows: added.extend(rows)})()
    tool = _unit_tool(object(), context, engine="kitesurf")
    tool._request_bridge_origins = frozenset({"https://admin.shop.test"})
    cookie = type(
        "Cookie",
        (),
        {
            "name": "admin",
            "value": "two",
            "path": "/",
            "secure": True,
            "expires": 1_900_000_000,
            "domain": ".shop.test",
            "domain_specified": True,
            "_rest": {},
        },
    )()

    tool._sync_request_bridge_cookies("https://admin.shop.test/", [cookie])

    assert added == [
        {
            "name": "admin",
            "value": "two",
            "domain": ".shop.test",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "expires": 1_900_000_000.0,
        }
    ]


def test_request_bridge_cookie_sync_rejects_cross_origin_response() -> None:
    tool = _unit_tool(object(), object(), engine="kitesurf")
    tool._request_bridge_origins = frozenset({"http://shop.test"})

    with pytest.raises(RuntimeError, match="origin is not allowed"):
        tool._sync_request_bridge_cookies("http://metadata.invalid/", [])


def test_login_form_bridge_rejects_cross_origin_redirect_before_fetch() -> None:
    calls = []

    class Redirect:
        status_code = 302
        url = "http://gitlab.test/users/sign_in"
        headers = {"location": "http://metadata.invalid/latest"}
        cookies = []

    class Session:
        def request(self, **kwargs):
            calls.append(kwargs)
            if len(calls) > 1:
                raise AssertionError("cross-origin redirect must not be fetched")
            return Redirect()

    tool = _unit_tool(object(), object(), engine="kitesurf")
    tool._request_bridge_session = Session()
    tool._sync_request_bridge_cookies = lambda *_args: None

    with pytest.raises(RuntimeError, match="cross-origin redirect"):
        tool._request_login_form(
            source_origin="http://gitlab.test",
            method="post",
            url="http://gitlab.test/users/sign_in",
            fields=[["user[login]", "alice"]],
            referer="http://gitlab.test/users/sign_in",
        )

    assert len(calls) == 1


def test_bridge_close_failure_still_closes_browser_and_playwright() -> None:
    events = []

    class BridgeSession:
        def close(self):
            events.append("bridge")
            raise RuntimeError("bridge close failed")

    tool = _unit_tool(object(), object(), engine="kitesurf")
    tool._request_bridge_session = BridgeSession()
    tool._layout_connection_poisoned = False
    tool._browser = type("Browser", (), {"close": lambda self: events.append("browser")})()
    tool._playwright = type(
        "Playwright", (), {"stop": lambda self: events.append("playwright")}
    )()

    with pytest.raises(RuntimeError, match="bridge close failed"):
        tool.close()

    assert events == ["bridge", "browser", "playwright"]


def test_kitesurf_login_readiness_uses_dom_polling() -> None:
    events = []

    class Page:
        def evaluate(self, script, argument):
            events.append(("evaluate", argument))
            assert "document.querySelectorAll(selector)" in script
            return sum(event[0] == "evaluate" for event in events) == 2

        def wait_for_timeout(self, timeout):
            events.append(("wait", timeout))

        def locator(self, _selector):
            raise AssertionError("Kitesurf readiness must not use Locator.wait_for")

    tool = _unit_tool(Page(), object(), engine="kitesurf")
    tool._wait_for_login_ready(
        tool._page, "button", exact_text="alice", timeout_ms=1_000
    )

    assert events == [
        ("evaluate", {"selector": "button", "exactText": "alice"}),
        ("wait", 100),
        ("evaluate", {"selector": "button", "exactText": "alice"}),
    ]


def test_obscura_viewport_verification_rejects_configured_but_unapplied_size() -> None:
    class Session:
        def send(self, method, _params=None):
            if method == "Page.getLayoutMetrics":
                return {
                    "cssVisualViewport": {"clientWidth": 800, "clientHeight": 600}
                }
            return {"result": {"value": 1}}

    page = type("Page", (), {"viewport_size": {"width": 1280, "height": 900}})()
    context = type(
        "Context",
        (),
        {"new_cdp_session": lambda self, selected: Session()},
    )()
    tool = _unit_tool(page, context, engine="obscura")
    tool._owns_cdp_context = True
    tool._supports_multiple_pages = False
    tool._browser = type("Browser", (), {"contexts": [context]})()
    tool._submit = lambda callback: callback()

    with pytest.raises(RuntimeError, match="viewport mismatch"):
        _prepare_browser_viewport(tool)


def test_moli_layout_publication_uses_explicit_geometry_only_cdp():
    events = []

    class Impl:
        async def send(self, method, params):
            events.append(("send", method, params))
            return {"layoutViewport": {"clientWidth": 1280, "clientHeight": 720}}

    class Session:
        _impl_obj = Impl()

        def _sync(self, coroutine):
            return asyncio.run(coroutine)

    page = object()
    context = type(
        "Context",
        (),
        {
            "new_cdp_session": lambda self, selected: (
                events.append(("session", selected)) or Session()
            )
        },
    )()

    _unit_tool(page, context, engine="moli")._publish_page_layout(page)

    assert events == [
        ("session", page),
        ("send", "Page.getLayoutMetrics", {"publishLayout": True}),
    ]


def test_chromium_layout_publication_is_a_strict_noop():
    class Context:
        def new_cdp_session(self, _page):
            raise AssertionError("Chromium must not receive Moli layout publication")

    tool = _unit_tool(object(), Context())
    assert tool.layout_observer is None
    tool._publish_page_layout(object())


def test_moli_layout_publication_fails_when_renderer_returns_no_metrics():
    class Impl:
        async def send(self, _method, _params):
            return {}

    class Session:
        _impl_obj = Impl()

        def _sync(self, coroutine):
            return asyncio.run(coroutine)

    context = type("Context", (), {"new_cdp_session": lambda self, _page: Session()})()

    with pytest.raises(RuntimeError, match="returned no layout metrics"):
        _unit_tool(object(), context, engine="moli")._publish_page_layout(object())


def test_moli_layout_publication_obeys_its_budget():
    class Impl:
        async def send(self, _method, _params):
            await asyncio.sleep(1)
            return {"layoutViewport": {"clientWidth": 1280, "clientHeight": 720}}

    class Session:
        _impl_obj = Impl()

        def _sync(self, coroutine):
            return asyncio.run(coroutine)

    context = type("Context", (), {"new_cdp_session": lambda self, _page: Session()})()
    started = time.monotonic()

    tool = _unit_tool(object(), context, engine="moli")
    with pytest.raises(TimeoutError, match="publication timed out"):
        tool._publish_page_layout(object(), timeout_ms=20)

    assert time.monotonic() - started < 0.2
    assert tool.failure_capture_available is False


def test_poisoned_layout_connection_skips_graceful_close():
    class MustNotClose:
        def close(self):
            raise AssertionError("poisoned renderer connection must not receive another command")

    tool = _unit_tool(object(), MustNotClose(), engine="moli")
    tool._browser = MustNotClose()
    stops = []
    tool._playwright = type("Playwright", (), {"stop": lambda self: stops.append("stop")})()
    tool._layout_connection_poisoned = True

    tool.close()
    assert stops == ["stop"]


def test_snapshot_retries_native_observation_after_navigation_settles():
    events = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            events.append(("visible", kwargs))

        def aria_snapshot(self, **kwargs):
            events.append(("aria_snapshot", kwargs))
            if sum(event[0] == "aria_snapshot" for event in events) == 1:
                raise RuntimeError("Execution context was destroyed because of a navigation")
            return "- main: New profile document"

    class Page:
        def locator(self, selector):
            events.append(("locator", selector))
            return Locator()

        def wait_for_load_state(self, state, **kwargs):
            events.append(("load_state", state, kwargs))

        def wait_for_timeout(self, milliseconds):
            events.append(("wait", milliseconds))

    result = _unit_tool(Page(), object()).execute({"action": "snapshot"})

    assert result.status == "success"
    assert result.result == "- main: New profile document"
    assert sum(event[0] == "aria_snapshot" for event in events) == 2
    assert any(event[:2] == ("load_state", "domcontentloaded") for event in events)


def test_snapshot_never_returns_old_document_when_navigation_does_not_settle():
    events = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            events.append("visible")

        def aria_snapshot(self, **kwargs):
            events.append("aria")
            raise RuntimeError("Execution context was destroyed because of a navigation")

    class Page:
        def locator(self, selector):
            return Locator()

        def wait_for_load_state(self, state, **kwargs):
            events.append("load_state")
            raise TimeoutError("new document did not reach DOMContentLoaded")

    result = _unit_tool(Page(), object()).execute({"action": "snapshot"})

    assert result.status == "error"
    assert "did not reach DOMContentLoaded" in result.result
    assert events == ["load_state", "visible", "aria", "load_state"]


def test_snapshot_does_not_hide_a_non_navigation_observation_error():
    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            pass

        def aria_snapshot(self, **kwargs):
            raise RuntimeError("Accessibility tree serialization failed")

    class Page:
        def locator(self, selector):
            return Locator()

    result = _unit_tool(Page(), object()).execute({"action": "snapshot"})
    assert result.status == "error"
    assert "Accessibility tree serialization failed" in result.result


def test_snapshot_hidden_selector_fails_before_observation():
    events = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            events.append(("visible", kwargs))
            raise TimeoutError("element remained hidden")

        def aria_snapshot(self, **kwargs):
            events.append(("aria", kwargs))
            return "old hidden document"

    class Page:
        def locator(self, selector):
            return Locator()

    result = _unit_tool(Page(), object()).execute(
        {"action": "snapshot", "selector": "#hidden", "timeout_ms": 20}
    )
    assert result.status == "error"
    assert "remained hidden" in result.result
    assert events
    assert all(event[0] == "visible" for event in events)


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
def test_playwright_backend_executes_portable_and_snapshot_ref_calls(tmp_path: Path) -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    page_path = tmp_path / "form.html"
    page_path.write_text(
        """<input id="name"><button id="save" onclick="
        document.querySelector('#result').textContent =
        document.querySelector('#name').value">Save</button><p id="result"></p>
        <p id="hidden" hidden>must not be observed</p>""",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        _browser_command(
            argparse.Namespace(engine="chromium", binary=binary),
            port,
            str(tmp_path / "chrome-profile"),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_cdp(endpoint, process)
        from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

        tool = PlaywrightReplayTool(endpoint)
        try:
            assert _prepare_browser_viewport(tool)["verified"]
            started = time.monotonic()
            status, _ = _execute_replay_call(
                tool,
                {"action": "navigate", "url": page_path.as_uri(), "timeout_ms": 1_000},
            )
            assert status == "success"
            assert time.monotonic() - started <= 1.5
            status, result = _execute_replay_call(
                tool,
                {
                    "action": "snapshot",
                    "selector": "#hidden",
                    "timeout_ms": 100,
                },
            )
            assert status == "error"
            assert "hidden" in result.lower()
            status, _ = _execute_replay_call(
                tool,
                {
                    "action": "fill",
                    "selector": "#name",
                    "text": "Chromium replay",
                    "replay_mode": "snapshot_ref_semantics",
                },
            )
            assert status == "success"
            status, _ = _execute_replay_call(
                tool,
                {
                    "action": "click",
                    "selector": "#save",
                    "replay_mode": "snapshot_ref_semantics",
                },
            )
            assert status == "success"
            status, result = _execute_replay_call(
                tool,
                {"action": "evaluate", "script": "document.querySelector('#result').textContent"},
            )
            assert (status, result) == ("success", "Chromium replay")
            status, result = _execute_replay_call(
                tool,
                {"action": "evaluate", "script": "({saved:true,count:1})"},
            )
            assert status == "success"
            assert json.loads(result) == {"saved": True, "count": 1}
            output: dict[str, str] = {}
            tool._submit(
                lambda: execute_steps(
                    tool._page,
                    [{"action": "get_text", "selector": "#result", "save_as": "saved"}],
                    outputs=output,
                )
            )
            assert output == {"saved": "Chromium replay"}
        finally:
            tool.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
