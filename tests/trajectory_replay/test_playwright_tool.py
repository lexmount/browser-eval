"""Exercise the CowAgent-free path against a real Chromium page."""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from harness.environments.webarena.runtime import (
    prepare_browser_viewport,
)
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.remote_execution import _browser_command, _wait_cdp
from harness.trajectory_replay.webarena_replay import _execute_replay_call


def test_request_bridge_host_override_preserves_logical_http_identity() -> None:
    from harness.trajectory_replay.playwright_tool import _HostOverrideAdapter

    adapter = _HostOverrideAdapter(
        (("*.dev.lexmount.net", "10.2.62.142"),)
    )
    calls = []

    class PoolManager:
        def connection_from_host(self, **kwargs):
            calls.append(kwargs)
            return "pool"

    adapter.poolmanager = PoolManager()
    prepared = requests.Session().prepare_request(
        requests.Request(
            "POST",
            "http://webarena-shopping.dev.lexmount.net:8080/graphql",
        )
    )

    assert adapter.get_connection_with_tls_context(prepared, True) == "pool"
    assert prepared.url == "http://webarena-shopping.dev.lexmount.net:8080/graphql"
    assert prepared.headers["Host"] == "webarena-shopping.dev.lexmount.net:8080"
    assert calls == [{"scheme": "http", "host": "10.2.62.142", "port": 8080}]

    https = requests.Session().prepare_request(
        requests.Request("GET", "https://webarena-shopping.dev.lexmount.net/")
    )
    with pytest.raises(ValueError, match="HTTP origins only"):
        adapter.get_connection_with_tls_context(https, True)


def test_request_bridge_installation_routes_matching_https_to_fail_closed() -> None:
    from harness.trajectory_replay.playwright_tool import (
        PlaywrightReplayTool,
        _HostOverrideAdapter,
    )

    class Context:
        def expose_binding(self, *_args):
            pass

        def add_init_script(self, *_args):
            pass

        def route(self, *_args):
            pass

    tool = PlaywrightReplayTool.__new__(PlaywrightReplayTool)
    tool._context = Context()
    tool.request_bridge_stats = {"requests": 0, "failures": 0}
    tool._install_request_bridge(
        {"https://shop.test"},
        [{"pattern": "shop.test", "address": "127.0.0.1"}],
    )
    try:
        assert isinstance(
            tool._request_bridge_session.adapters["https://"],
            _HostOverrideAdapter,
        )
        with pytest.raises(ValueError, match="HTTP origins only"):
            tool._request_bridge_session.get("https://shop.test/", timeout=1)
    finally:
        tool._request_bridge_session.close()


def test_request_bridge_host_override_routes_bytes_and_keeps_cookie_domain() -> None:
    from harness.trajectory_replay.playwright_tool import _HostOverrideAdapter

    observed = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers["Host"]))
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=value; Domain=shop.test; Path=/")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        session = requests.Session()
        session.trust_env = False
        session.mount(
            "http://",
            _HostOverrideAdapter((("shop.test", "127.0.0.1"),)),
        )
        response = session.get(
            f"http://shop.test:{server.server_port}/resource", timeout=5
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status_code == 200
    assert response.url == f"http://shop.test:{server.server_port}/resource"
    assert response.cookies.get("sid", domain=".shop.test", path="/") == "value"
    assert observed == [("/resource", f"shop.test:{server.server_port}")]


def test_request_bridge_host_override_prefers_exact_then_specific_wildcard() -> None:
    from harness.trajectory_replay.playwright_tool import (
        _HostOverrideAdapter,
        _validated_host_overrides,
    )

    adapter = _HostOverrideAdapter(
        _validated_host_overrides(
            [
                {"pattern": "*.dev.lexmount.net", "address": "10.0.0.1"},
                {"pattern": "*.sub.dev.lexmount.net", "address": "10.0.0.2"},
                {"pattern": "shop.sub.dev.lexmount.net", "address": "10.0.0.3"},
            ]
        )
    )

    assert adapter._address_for("shop.sub.dev.lexmount.net") == "10.0.0.3"
    assert adapter._address_for("other.sub.dev.lexmount.net") == "10.0.0.2"
    assert adapter._address_for("other.dev.lexmount.net") == "10.0.0.1"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ([{"pattern": "shop.*.test", "address": "10.2.62.142"}], "pattern"),
        ([{"pattern": "*.dev.lexmount.net", "address": "internal.test"}], "address"),
        (
            [
                {"pattern": "*.dev.lexmount.net", "address": "10.2.62.142"},
                {"pattern": "*.dev.lexmount.net", "address": "10.2.62.143"},
            ],
            "duplicated",
        ),
    ],
)
def test_request_bridge_host_override_rejects_ambiguous_inputs(
    overrides, message
) -> None:
    from harness.trajectory_replay.playwright_tool import _validated_host_overrides

    with pytest.raises(ValueError, match=message):
        _validated_host_overrides(overrides)


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
        viewport = prepare_browser_viewport(tool)
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

        def expose_binding(self, name, callback):
            events.append(("binding", name, callback))

        def add_init_script(self, script):
            events.append(("init_script", script))

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
    binding_event = next(event for event in events if event[0] == "binding")
    assert binding_event[1] == "__browserEvalCaptureRequestBody"
    assert "globalThis.fetch = async function" in next(
        event[1] for event in events if event[0] == "init_script"
    )
    init_script = next(event[1] for event in events if event[0] == "init_script")
    assert "headers, body: ''" in init_script
    assert "application/x-www-form-urlencoded;charset=UTF-8" in init_script
    assert "text/plain;charset=UTF-8" in init_script
    route_event = next(event for event in events if event[0] == "route")
    predicate, handler = route_event[1:]
    assert predicate("http://private.test/path") is True
    assert predicate("https://public.test/path") is False

    class Request:
        method = "POST"
        url = "http://private.test/save"
        post_data_buffer = None
        resource_type = "xhr"

        def __init__(self, capture_id):
            self.capture_id = capture_id

        def all_headers(self):
            return {
                "content-type": "application/json",
                "content-length": "16",
                "signature": "private-signature",
                "x-browser-eval-request-body-id": self.capture_id,
            }

    class Route:
        request = Request("capture-1")

        def fulfill(self, **kwargs):
            events.append(("bridge", "fulfill", kwargs))

    captured_payload = {
        "requestId": "capture-1",
        "method": "POST",
        "url": "http://private.test/save",
        "body": {"kind": "text", "text": '{"enabled":true}'},
    }
    binding_event[2]({}, captured_payload)
    handler(Route())
    assert tool.request_bridge_stats == {"requests": 1, "failures": 0}

    class FailedRoute:
        request = Request("capture-2")

        def abort(self, reason):
            events.append(("bridge", "abort", reason))

    binding_event[2]({}, {**captured_payload, "requestId": "capture-2"})
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
        ("binding", "__browserEvalCaptureRequestBody", binding_event[2]),
        ("init_script", next(event[1] for event in events if event[0] == "init_script")),
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


def test_kitesurf_request_bridge_rebuilds_captured_form_data(monkeypatch) -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    class Context:
        def expose_binding(self, _name, callback):
            self.capture = callback

        def add_init_script(self, script):
            assert "body instanceof FormData" in script

        def route(self, _selector, handler):
            self.bridge = handler

    context = Context()
    calls = []

    class Response:
        status_code = 201
        headers = {"content-type": "application/json"}
        content = b"{}"
        url = "http://private.test/upload"
        cookies = []

    class Session:
        trust_env = True

        def request(self, **kwargs):
            calls.append(kwargs)
            return Response()

    monkeypatch.setattr(
        "harness.trajectory_replay.playwright_tool.requests.Session", Session
    )
    tool = PlaywrightReplayTool.__new__(PlaywrightReplayTool)
    tool._context = context
    tool.request_bridge_stats = {"requests": 0, "failures": 0}
    tool._install_request_bridge({"http://private.test"})
    context.capture(
        {},
        {
            "requestId": "form-1",
            "method": "POST",
            "url": "http://private.test/upload",
            "body": {
                "kind": "form-data",
                "entries": [
                    {"name": "title", "value": "hello"},
                    {
                        "name": "image",
                        "file": {
                            "name": "",
                            "type": "application/octet-stream",
                            "base64": "",
                        },
                    },
                ],
            },
        },
    )

    class Request:
        method = "POST"
        url = "http://private.test/upload"
        post_data_buffer = None

        def all_headers(self):
            return {
                "content-type": "multipart/form-data; boundary=browser-boundary",
                "x-csrf-token": "csrf",
                "x-browser-eval-request-body-id": "form-1",
            }

    class Route:
        request = Request()

        def fulfill(self, **_kwargs):
            pass

    context.bridge(Route())

    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://private.test/upload"
    assert call["headers"]["x-csrf-token"] == "csrf"
    assert call["headers"]["content-type"].startswith(
        "multipart/form-data; boundary=browser-eval-"
    )
    assert b'name="title"\r\n\r\nhello\r\n' in call["data"]
    assert (
        b'name="image"; filename=""\r\nContent-Type: application/octet-stream\r\n\r\n\r\n'
        in call["data"]
    )
    assert call["data"].endswith(b"--\r\n")
    assert call["allow_redirects"] is False
    assert call["timeout"] == 60


def test_kitesurf_request_capture_ids_prevent_same_url_body_reuse() -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    tool = PlaywrightReplayTool.__new__(PlaywrightReplayTool)
    tool._request_bridge_origins = frozenset({"http://private.test"})
    tool._request_bridge_bodies = {}
    tool._request_bridge_body_bytes = 0
    tool._request_bridge_har_bodies = {}
    tool._request_bridge_har_body_bytes = 0
    for request_id, value in (("aborted-1", "old"), ("sent-2", "new")):
        tool._capture_request_body(
            {},
            {
                "requestId": request_id,
                "method": "POST",
                "url": "http://private.test/save",
                "body": {"kind": "text", "text": value},
            },
        )

    assert tool._pop_captured_request_body(
        "sent-2", "POST", "http://private.test/save"
    ) == {"kind": "text", "text": "new"}
    assert "aborted-1" in tool._request_bridge_bodies
    with pytest.raises(RuntimeError, match="does not match"):
        tool._pop_captured_request_body(
            "aborted-1", "PUT", "http://private.test/save"
        )


def test_kitesurf_native_form_body_uses_capture_id(monkeypatch) -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    class Context:
        def expose_binding(self, _name, callback):
            self.capture = callback

        def add_init_script(self, script):
            assert "HTMLFormElement.prototype.submit" in script

        def route(self, _selector, handler):
            self.bridge = handler

    context = Context()
    calls = []

    class Response:
        status_code = 302
        headers = {"location": "/issues/1"}
        content = b""
        url = "http://private.test/issues"
        cookies = []

    class Session:
        trust_env = True

        def request(self, **kwargs):
            calls.append(kwargs)
            return Response()

    monkeypatch.setattr(
        "harness.trajectory_replay.playwright_tool.requests.Session", Session
    )
    tool = PlaywrightReplayTool.__new__(PlaywrightReplayTool)
    tool._context = context
    tool.request_bridge_stats = {"requests": 0, "failures": 0}
    tool._install_request_bridge({"http://private.test"})
    context.capture(
        {},
        {
            "requestId": "native-form-1",
            "method": "POST",
            "url": "http://private.test/issues",
            "source": "native-form",
            "body": {"kind": "text", "text": "issue%5Btitle%5D=hello"},
        },
    )

    class Request:
        method = "POST"
        url = "http://private.test/issues"
        post_data_buffer = None

        def all_headers(self):
            return {
                "content-type": "application/x-www-form-urlencoded",
                "x-browser-eval-request-body-id": "native-form-1",
            }

    class Route:
        request = Request()

        def fulfill(self, **_kwargs):
            pass

    context.bridge(Route())

    assert calls[0]["data"] == b"issue%5Btitle%5D=hello"
    headers, body, response_headers = tool.transform_playwright_har_request(
        "POST",
        "http://private.test/issues",
        {"x-browser-eval-request-body-id": "native-form-1"},
    )
    assert headers == {}
    assert body == "issue%5Btitle%5D=hello"
    assert response_headers == {
        "status": 302,
        "statusText": "",
        "headers": {"location": "/issues/1"},
    }


def test_kitesurf_request_bridge_encodes_empty_form_data() -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    data, content_type = PlaywrightReplayTool._decode_captured_request_body(
        "empty-form", {"kind": "form-data", "entries": []}
    )
    boundary = content_type.removeprefix("multipart/form-data; boundary=")
    assert data == f"--{boundary}--\r\n".encode()


@pytest.mark.parametrize("content_type", ["text/plain\r\nX-Test: injected", "text/plain\n"])
def test_kitesurf_request_bridge_rejects_newlines_in_file_content_type(
    content_type: str,
) -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    body = {
        "kind": "form-data",
        "entries": [
            {
                "name": "upload",
                "file": {"name": "note.txt", "type": content_type, "base64": ""},
            }
        ],
    }
    with pytest.raises(ValueError, match="content type contains a newline"):
        PlaywrightReplayTool._decode_captured_request_body("unsafe-type", body)


def test_kitesurf_request_body_id_survives_post_redirect_as_bodyless_get(
    monkeypatch,
) -> None:
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    class Context:
        def expose_binding(self, _name, callback):
            self.capture = callback

        def add_init_script(self, _script):
            pass

        def route(self, _selector, handler):
            self.bridge = handler

    context = Context()
    calls = []

    class Response:
        cookies = []

        def __init__(self, status, url, headers):
            self.status_code = status
            self.url = url
            self.headers = headers
            self.content = b""

    class Session:
        trust_env = True

        def request(self, **kwargs):
            calls.append(kwargs)
            if kwargs["url"].endswith("/start"):
                return Response(302, kwargs["url"], {"location": "/final"})
            if kwargs["url"].endswith("/keep"):
                return Response(307, kwargs["url"], {"location": "/kept"})
            return Response(200, kwargs["url"], {"content-type": "text/plain"})

    monkeypatch.setattr(
        "harness.trajectory_replay.playwright_tool.requests.Session", Session
    )
    tool = PlaywrightReplayTool.__new__(PlaywrightReplayTool)
    tool._context = context
    tool.request_bridge_stats = {"requests": 0, "failures": 0}
    tool._install_request_bridge({"http://private.test"})
    context.capture(
        {},
        {
            "requestId": "redirect-1",
            "method": "POST",
            "url": "http://private.test/start",
            "body": {"kind": "text", "text": "enabled=true"},
        },
    )

    class Request:
        post_data_buffer = None

        def __init__(self, method, url):
            self.method = method
            self.url = url

        def all_headers(self):
            return {"x-browser-eval-request-body-id": "redirect-1"}

    class Route:
        def __init__(self, request):
            self.request = request

        def fulfill(self, **_kwargs):
            pass

    context.bridge(Route(Request("POST", "http://private.test/start")))
    context.bridge(Route(Request("GET", "http://private.test/final")))

    context.capture(
        {},
        {
            "requestId": "redirect-2",
            "method": "POST",
            "url": "http://private.test/keep",
            "body": {"kind": "text", "text": "kept=true"},
        },
    )

    class KeptRequest(Request):
        def all_headers(self):
            return {"x-browser-eval-request-body-id": "redirect-2"}

    context.bridge(Route(KeptRequest("POST", "http://private.test/keep")))
    context.bridge(Route(KeptRequest("POST", "http://private.test/kept")))

    assert [call["data"] for call in calls] == [
        b"enabled=true",
        None,
        b"kept=true",
        b"kept=true",
    ]
    original_headers, original_body, _ = tool.transform_playwright_har_request(
        "POST",
        "http://private.test/start",
        {"x-browser-eval-request-body-id": "redirect-1"},
    )
    redirect_headers, redirect_body, _ = tool.transform_playwright_har_request(
        "GET",
        "http://private.test/final",
        {"x-browser-eval-request-body-id": "redirect-1"},
    )
    assert original_headers == redirect_headers == {}
    assert original_body == "enabled=true"
    assert redirect_body is None
    _, kept_body, _ = tool.transform_playwright_har_request(
        "POST",
        "http://private.test/kept",
        {"x-browser-eval-request-body-id": "redirect-2"},
    )
    assert kept_body == "kept=true"


def test_kitesurf_request_capture_bounds_pending_and_retained_records(
    monkeypatch,
) -> None:
    from harness.trajectory_replay import playwright_tool as module

    tool = module.PlaywrightReplayTool.__new__(module.PlaywrightReplayTool)
    tool._request_bridge_origins = frozenset({"http://private.test"})
    tool._request_bridge_bodies = {}
    tool._request_bridge_body_bytes = 0
    tool._request_bridge_har_bodies = {"finished": {}}
    tool._request_bridge_har_body_bytes = 0
    monkeypatch.setattr(module, "REQUEST_BODY_CAPTURE_MAX_PENDING", 1)

    with pytest.raises(RuntimeError, match="count exceeded"):
        tool._capture_request_body(
            {},
            {
                "requestId": "next",
                "method": "POST",
                "url": "http://private.test/save",
                "body": {"kind": "text", "text": ""},
            },
        )
    monkeypatch.setattr(module, "REQUEST_BODY_CAPTURE_MAX_PENDING", 2)
    with pytest.raises(ValueError, match="reused"):
        tool._capture_request_body(
            {},
            {
                "requestId": "finished",
                "method": "POST",
                "url": "http://private.test/save",
                "body": {"kind": "text", "text": ""},
            },
        )


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


def test_kitesurf_document_observer_uses_page_evaluate_without_locator() -> None:
    class Page:
        def evaluate(self, script, limit):
            assert "document.body?.innerText" in script
            assert limit == 123
            return {"title": "Dashboard", "body": "Visible body"}

        def locator(self, _selector):
            raise AssertionError("Kitesurf document observation must not use Locator")

    tool = _unit_tool(Page(), object(), engine="kitesurf")

    assert tool.document_observer(tool._page, 1_000, 123) == (
        "Dashboard",
        "Visible body",
    )


def test_kitesurf_document_observer_enforces_action_timeout() -> None:
    class Page:
        def evaluate(self, _script, _limit):
            time.sleep(1)

    tool = _unit_tool(Page(), object(), engine="kitesurf")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="exceeded 20 ms"):
        tool.document_observer(tool._page, 20, 123)
    assert time.monotonic() - started < 0.5


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
        prepare_browser_viewport(tool)


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
def test_kitesurf_native_form_capture_freezes_one_submission_snapshot() -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    from playwright.sync_api import sync_playwright

    from harness.trajectory_replay.playwright_tool import (
        NATIVE_FORM_REDIRECT_HEADER,
        REQUEST_BODY_CAPTURE_BINDING,
        REQUEST_BODY_CAPTURE_HEADER,
        _request_body_capture_script,
    )

    captures = []
    posts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(binary), headless=True)
        context = browser.new_context()
        context.expose_binding(
            REQUEST_BODY_CAPTURE_BINDING,
            lambda _source, payload: captures.append(payload),
        )
        context.add_init_script(_request_body_capture_script(frozenset({"http://capture.test"})))

        gets = []

        def route_request(route):
            request = route.request
            if request.method == "POST":
                posts.append(
                    {
                        "url": request.url,
                        "headers": request.all_headers(),
                        "body": request.post_data,
                    }
                )
                route.fulfill(
                    status=200,
                    headers={NATIVE_FORM_REDIRECT_HEADER: "http://capture.test/save"},
                    body="",
                )
                return
            gets.append({"url": request.url, "resource_type": request.resource_type})
            if request.url.endswith("/form"):
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=(
                        '<form action="/save" method="post">'
                        '<textarea name="note"></textarea>'
                        '<input name="x" value="one">'
                        "</form>"
                    ),
                )
                return
            route.fulfill(status=200, content_type="text/html", body='<p id="done">done</p>')

        context.route("http://capture.test/**", route_request)
        page = context.new_page()
        page.goto("http://capture.test/form")
        page.evaluate(
            """() => {
                const form = document.querySelector('form');
                let count = 0;
                form.addEventListener('formdata', event => {
                    event.formData.append('nonce', String(++count));
                });
                form.elements.note.value = 'a\\nb';
                form.submit();
                form.action = '/other';
                form.elements.x.value = 'two';
                form.remove();
            }"""
        )
        page.wait_for_url("http://capture.test/save")
        browser.close()

    assert len(captures) == 1
    assert captures[0]["url"] == "http://capture.test/save"
    assert captures[0]["body"] == {
        "kind": "text",
        "text": "note=a%0D%0Ab&x=one&nonce=1",
    }
    assert len(posts) == 1
    assert posts[0]["url"] == "http://capture.test/save"
    assert posts[0]["body"] == "note=a%0D%0Ab&x=one&nonce=1"
    assert posts[0]["headers"][REQUEST_BODY_CAPTURE_HEADER] == captures[0]["requestId"]
    assert gets == [
        {"url": "http://capture.test/form", "resource_type": "document"},
        {"url": "http://capture.test/save", "resource_type": "document"},
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
def test_kitesurf_fetch_capture_keeps_body_only_in_local_bridge() -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    from playwright.sync_api import sync_playwright

    from harness.trajectory_replay.playwright_tool import (
        REQUEST_BODY_CAPTURE_BINDING,
        REQUEST_BODY_CAPTURE_HEADER,
        _request_body_capture_script,
    )

    captures = []
    posts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(binary), headless=True)
        context = browser.new_context()
        context.expose_binding(
            REQUEST_BODY_CAPTURE_BINDING,
            lambda _source, payload: captures.append(payload),
        )
        context.add_init_script(
            _request_body_capture_script(frozenset({"http://capture.test"}))
        )

        def route_request(route):
            request = route.request
            if request.method == "POST":
                posts.append(
                    {
                        "headers": request.all_headers(),
                        "body": request.post_data,
                    }
                )
                route.fulfill(status=200, content_type="application/json", body="{}")
                return
            route.fulfill(status=200, content_type="text/html", body="<p>ready</p>")

        context.route("http://capture.test/**", route_request)
        page = context.new_page()
        page.goto("http://capture.test/")
        status = page.evaluate(
            """async () => (await fetch('/graphql', {
                method: 'POST',
                headers: {'content-type': 'application/json'},
                body: JSON.stringify({page: 1}),
            })).status"""
        )
        browser.close()

    assert status == 200
    assert len(captures) == 1
    assert captures[0]["body"] == {"kind": "text", "text": '{"page":1}'}
    assert len(posts) == 1
    assert posts[0]["body"] is None
    assert posts[0]["headers"]["content-type"] == "application/json"
    assert posts[0]["headers"][REQUEST_BODY_CAPTURE_HEADER] == captures[0]["requestId"]


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_kitesurf_fetch_capture_rejects_get_and_head_bodies(method: str) -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    from playwright.sync_api import sync_playwright

    from harness.trajectory_replay.playwright_tool import (
        REQUEST_BODY_CAPTURE_BINDING,
        _request_body_capture_script,
    )

    captures = []
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(binary), headless=True)
        context = browser.new_context()
        context.expose_binding(
            REQUEST_BODY_CAPTURE_BINDING,
            lambda _source, payload: captures.append(payload),
        )
        context.add_init_script(
            _request_body_capture_script(frozenset({"http://capture.test"}))
        )

        def route_request(route):
            requests.append(route.request.url)
            route.fulfill(status=200, content_type="text/html", body="<p>ready</p>")

        context.route("http://capture.test/**", route_request)
        page = context.new_page()
        page.goto("http://capture.test/")
        result = page.evaluate(
            """async method => {
                try {
                    await fetch('/query', {method, body: 'invalid'});
                    return 'resolved';
                } catch (error) {
                    return [error.name, error.message];
                }
            }""",
            method,
        )
        browser.close()

    assert result[0] == "TypeError"
    assert "GET/HEAD" in result[1]
    assert captures == []
    assert requests == ["http://capture.test/"]


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
def test_kitesurf_fetch_capture_preserves_keepalive_body_limit() -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    from playwright.sync_api import sync_playwright

    from harness.trajectory_replay.playwright_tool import (
        REQUEST_BODY_CAPTURE_BINDING,
        _request_body_capture_script,
    )

    captures = []
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(binary), headless=True)
        context = browser.new_context()
        context.expose_binding(
            REQUEST_BODY_CAPTURE_BINDING,
            lambda _source, payload: captures.append(payload),
        )
        context.add_init_script(
            _request_body_capture_script(frozenset({"http://capture.test"}))
        )

        def route_request(route):
            requests.append(
                {
                    "url": route.request.url,
                    "method": route.request.method,
                    "body": route.request.post_data,
                }
            )
            route.fulfill(status=200, content_type="text/html", body="<p>ready</p>")

        context.route("http://capture.test/**", route_request)
        page = context.new_page()
        page.goto("http://capture.test/")
        result = page.evaluate(
            """async () => {
                const run = async body => {
                    try {
                        const response = await fetch('/save', {
                            method: 'POST', keepalive: true, body,
                        });
                        return ['resolved', response.status];
                    } catch (error) {
                        return [error.name, error.message];
                    }
                };
                const emptyForm = new FormData();
                emptyForm.set('value', '');
                const multipartOverhead = (
                    await new Request('/save', {method: 'POST', body: emptyForm})
                        .arrayBuffer()
                ).byteLength;
                const oversizedForm = new FormData();
                oversizedForm.set('value', 'x'.repeat(65537 - multipartOverhead));
                return [
                    await run('x'.repeat(65536)),
                    await run('x'.repeat(65537)),
                    await run(oversizedForm),
                ];
            }"""
        )
        browser.close()

    assert result[0] == ["resolved", 200]
    assert result[1][0] == "TypeError"
    assert result[2][0] == "TypeError"
    assert len(captures) == 1
    assert len(captures[0]["body"]["text"]) == 65536
    assert requests == [
        {"url": "http://capture.test/", "method": "GET", "body": None},
        {"url": "http://capture.test/save", "method": "POST", "body": None},
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
def test_kitesurf_fetch_capture_shares_and_releases_keepalive_budget() -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    from playwright.sync_api import sync_playwright

    from harness.trajectory_replay.playwright_tool import (
        REQUEST_BODY_CAPTURE_BINDING,
        _request_body_capture_script,
    )

    captures = []
    held_routes = []
    requested_urls = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(binary), headless=True)
        context = browser.new_context()
        context.expose_binding(
            REQUEST_BODY_CAPTURE_BINDING,
            lambda _source, payload: captures.append(payload),
        )
        context.add_init_script(
            _request_body_capture_script(frozenset({"http://capture.test"}))
        )

        def route_request(route):
            requested_urls.append(route.request.url)
            if route.request.url.endswith("/hold"):
                held_routes.append(route)
                return
            route.fulfill(
                status=200,
                headers={"access-control-allow-origin": "*"},
                content_type="text/html",
                body="<p>ready</p>",
            )

        context.route("**/*", route_request)
        page = context.new_page()
        page.goto("http://capture.test/")

        for first, second in (
            ("http://outside.test/hold", "http://capture.test/rejected"),
            ("http://capture.test/hold", "http://outside.test/rejected"),
        ):
            result = page.evaluate(
                """async ([first, second]) => {
                    window.pendingKeepalive = fetch(first, {
                        method: 'POST', keepalive: true, body: 'x'.repeat(40000),
                    }).then(() => 'resolved', error => error.name);
                    await new Promise(resolve => setTimeout(resolve, 50));
                    try {
                        await fetch(second, {
                            method: 'POST', keepalive: true, body: 'x'.repeat(40000),
                        });
                        return 'resolved';
                    } catch (error) {
                        return error.name;
                    }
                }""",
                [first, second],
            )
            assert result == "TypeError"
            assert held_routes
            held_routes.pop(0).abort("failed")
            assert page.evaluate("window.pendingKeepalive") == "TypeError"
            assert (
                page.evaluate(
                    """async () => (await fetch('/recovered', {
                        method: 'POST', keepalive: true, body: 'x'.repeat(40000),
                    })).status"""
                )
                == 200
            )

        browser.close()

    assert held_routes == []
    assert "http://capture.test/rejected" not in requested_urls
    assert "http://outside.test/rejected" not in requested_urls
    assert requested_urls.count("http://capture.test/recovered") == 2
    assert len(captures) == 3


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
def test_kitesurf_fetch_capture_allows_replacement_body_for_used_request() -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    from playwright.sync_api import sync_playwright

    from harness.trajectory_replay.playwright_tool import (
        REQUEST_BODY_CAPTURE_BINDING,
        _request_body_capture_script,
    )

    captures = []
    posts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(binary), headless=True)
        context = browser.new_context()
        context.expose_binding(
            REQUEST_BODY_CAPTURE_BINDING,
            lambda _source, payload: captures.append(payload),
        )
        context.add_init_script(
            _request_body_capture_script(frozenset({"http://capture.test"}))
        )

        def route_request(route):
            if route.request.method == "POST":
                posts.append((route.request.url, route.request.post_data))
            route.fulfill(
                status=200,
                headers={"access-control-allow-origin": "*"},
                content_type="text/html",
                body="<p>ready</p>",
            )

        context.route("**/*", route_request)
        page = context.new_page()
        page.goto("http://capture.test/")
        results = page.evaluate(
            """async () => {
                const replace = async url => {
                    const request = new Request(url, {method: 'POST', body: 'old'});
                    await request.text();
                    try {
                        return ['ok', (await fetch(request, {body: 'new'})).status];
                    } catch (error) {
                        return [error.name, error.message];
                    }
                };
                return [
                    await replace('http://capture.test/selected'),
                    await replace('http://outside.test/native'),
                ];
            }"""
        )
        browser.close()

    assert results == [["ok", 200], ["ok", 200]]
    assert captures[0]["body"] == {"kind": "text", "text": "new"}
    assert posts == [
        ("http://capture.test/selected", None),
        ("http://outside.test/native", "new"),
    ]


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
            assert prepare_browser_viewport(tool)["verified"]
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
