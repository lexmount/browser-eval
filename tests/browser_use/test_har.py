from __future__ import annotations

import json
from types import MappingProxyType

from webarena_verified.types.tracing import NetworkEvent

from harness.browser_use.har import CdpHarRecorder


class _Session:
    def __init__(self) -> None:
        self.handlers = {}
        self.detached = False
        self.on_get_response_body = None

    def on(self, event, callback):
        self.handlers[event] = callback

    def send(self, method, params=None):
        if method == "Network.getResponseBody":
            if self.on_get_response_body is not None:
                callback, self.on_get_response_body = self.on_get_response_body, None
                callback()
            return {"body": '{"ok":true}', "base64Encoded": False}
        return {}

    def detach(self):
        self.detached = True


class _Context:
    def __init__(self) -> None:
        self.pages = []
        self.sessions = []
        self.page_callback = None

    def new_cdp_session(self, _page):
        session = _Session()
        self.sessions.append(session)
        return session

    def on(self, event, callback):
        assert event == "page"
        self.page_callback = callback

    def remove_listener(self, event, callback):
        assert event == "page"
        assert callback == self.page_callback


class _Page:
    def __init__(self, context, on_wait=None) -> None:
        self.context = context
        self.on_wait = on_wait

    def wait_for_timeout(self, _timeout_ms):
        if self.on_wait is not None:
            callback, self.on_wait = self.on_wait, None
            callback()


def test_har_recorder_captures_new_tabs_and_evaluator_fields(tmp_path):
    context = _Context()
    page = _Page(context)
    context.pages.append(page)
    recorder = CdpHarRecorder(page)
    second = _Page(context)
    context.pages.append(second)
    context.page_callback(second)
    recorder.ensure_pages()
    assert len(context.sessions) == 2
    session = context.sessions[1]
    session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "7",
            "wallTime": 1,
            "request": {
                "method": "POST",
                "url": "https://example.test/save?q=1",
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer request-secret",
                    "Cookie": (
                        "sid=request-cookie; _osm_directions_engine=fossgis_osrm_car"
                    ),
                    "X-M2-Customer-Auto-Login": "user:password",
                },
                "postData": '{"name":"Ada"}',
            },
        }
    )
    session.handlers["Network.requestWillBeSentExtraInfo"](
        {
            "requestId": "7",
            "headers": {
                "Accept": "text/html,application/xhtml+xml",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Authorization": "Bearer extra-request-secret",
                "Cookie": (
                    "sid=extra-request-cookie; _osm_directions_engine=fossgis_osrm_car"
                ),
            },
        }
    )
    session.handlers["Network.responseReceived"](
        {
            "requestId": "7",
            "response": {
                "status": 200,
                "statusText": "OK",
                "protocol": "h2",
                "headers": {
                    "content-type": "application/json",
                    "set-cookie": (
                        "sid=response-cookie; Path=/\r\n"
                        "mage-messages=product%20added; Path=/"
                    ),
                },
                "mimeType": "application/json",
                "encodedDataLength": 11,
            },
        }
    )
    session.handlers["Network.responseReceivedExtraInfo"](
        {
            "requestId": "7",
            "headers": {
                "Content-Type": "application/json; charset=utf-8",
                "Set-Cookie": (
                    "sid=extra-response-cookie; Path=/\r\n"
                    "mage-messages=product%20saved; Path=/"
                ),
                "Location": "/complete",
            },
        }
    )
    session.handlers["Network.loadingFinished"]({"requestId": "7"})
    output = tmp_path / "network.har"

    summary = recorder.stop(output)

    payload = json.loads(output.read_text())
    assert payload["log"]["creator"] == {"name": "browser-eval-cdp", "version": "3"}
    entry = payload["log"]["entries"][0]
    assert summary == {
        "entry_count": 1,
        "request_count": 1,
        "missing_response_count": 0,
        "failed_request_count": 0,
        "unfinished_request_count": 0,
    }
    assert entry["request"]["postData"] == {
        "mimeType": "application/json",
        "text": '{"name":"Ada"}',
    }
    assert entry["response"]["content"]["text"] == '{"ok":true}'
    request_headers = {item["name"].lower(): item["value"] for item in entry["request"]["headers"]}
    response_headers = {
        item["name"].lower(): item["value"] for item in entry["response"]["headers"]
    }
    assert request_headers == {
        "content-type": "application/json",
        "authorization": "[REDACTED]",
        "cookie": "_osm_directions_engine=fossgis_osrm_car; [REDACTED]",
        "x-m2-customer-auto-login": "[REDACTED]",
        "accept": "text/html,application/xhtml+xml",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-user": "?1",
    }
    assert response_headers["set-cookie"] == "[REDACTED]"
    assert entry["response"]["cookies"] == [
        {"name": "sid", "value": "[REDACTED]"},
        {"name": "mage-messages", "value": "product%20saved"},
    ]
    assert entry["response"]["redirectURL"] == "/complete"
    navigation_entry = {
        **entry,
        "request": {**entry["request"], "method": "GET"},
    }
    assert NetworkEvent(data=MappingProxyType(navigation_entry)).is_navigation_event
    serialized = output.read_text()
    assert "request-secret" not in serialized
    assert "request-cookie" not in serialized
    assert "response-cookie" not in serialized
    assert "user:password" not in serialized
    assert "extra-request-secret" not in serialized
    assert "extra-request-cookie" not in serialized
    assert "extra-response-cookie" not in serialized
    assert "product%20saved" in serialized
    assert all(item.detached for item in context.sessions)


def test_har_recorder_drains_pending_response_before_writing(tmp_path):
    context = _Context()
    page = _Page(context)
    context.pages.append(page)
    recorder = CdpHarRecorder(page)
    session = context.sessions[0]
    session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "pending",
            "wallTime": 1,
            "request": {
                "method": "GET",
                "url": "https://example.test/result",
                "headers": {},
            },
        }
    )

    def finish_response():
        session.handlers["Network.responseReceived"](
            {
                "requestId": "pending",
                "response": {
                    "status": 200,
                    "headers": {"content-type": "application/json"},
                    "mimeType": "application/json",
                },
            }
        )
        session.handlers["Network.loadingFinished"]({"requestId": "pending"})

    page.on_wait = finish_response
    summary = recorder.stop(tmp_path / "network.har")

    assert summary["entry_count"] == 1
    assert summary["missing_response_count"] == 0
    assert summary["unfinished_request_count"] == 0


def test_har_recorder_preserves_post_redirect_hop_when_cdp_reuses_request_id(tmp_path):
    context = _Context()
    page = _Page(context)
    context.pages.append(page)
    recorder = CdpHarRecorder(page)
    session = context.sessions[0]
    session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "redirected",
            "wallTime": 1,
            "request": {
                "method": "POST",
                "url": "https://example.test/newsletter/subscriber/new",
                "headers": {"content-type": "application/x-www-form-urlencoded"},
                "postData": "email=reader%40example.test",
            },
        }
    )
    session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "redirected",
            "wallTime": 2,
            "redirectResponse": {
                "status": 302,
                "statusText": "Found",
                "protocol": "http/1.1",
                "headers": {"location": "https://example.test/"},
                "mimeType": "text/html",
            },
            "request": {
                "method": "GET",
                "url": "https://example.test/",
                "headers": {},
            },
        }
    )
    session.handlers["Network.responseReceived"](
        {
            "requestId": "redirected",
            "response": {
                "status": 200,
                "statusText": "OK",
                "headers": {"content-type": "text/html"},
                "mimeType": "text/html",
            },
        }
    )
    session.handlers["Network.loadingFinished"]({"requestId": "redirected"})

    summary = recorder.stop(tmp_path / "network.har")
    entries = json.loads((tmp_path / "network.har").read_text())["log"]["entries"]

    assert summary["request_count"] == 2
    assert summary["entry_count"] == 2
    assert entries[0]["request"]["method"] == "POST"
    assert entries[0]["request"]["postData"]["text"] == (
        "email=reader%40example.test"
    )
    assert entries[0]["response"]["status"] == 302
    assert entries[0]["response"]["redirectURL"] == "https://example.test/"
    assert entries[1]["request"]["method"] == "GET"
    assert entries[1]["response"]["status"] == 200


def test_har_recorder_uses_stable_event_snapshot_while_reading_bodies(tmp_path):
    context = _Context()
    page = _Page(context)
    context.pages.append(page)
    recorder = CdpHarRecorder(page)
    session = context.sessions[0]
    request = {
        "requestId": "first",
        "request": {
            "method": "GET",
            "url": "https://example.test/first",
            "headers": {},
        },
    }
    response = {
        "requestId": "first",
        "response": {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "mimeType": "application/json",
        },
    }
    session.handlers["Network.requestWillBeSent"](request)
    session.handlers["Network.responseReceived"](response)
    session.handlers["Network.loadingFinished"]({"requestId": "first"})
    session.on_get_response_body = lambda: session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "late",
            "request": {
                "method": "GET",
                "url": "https://example.test/late",
                "headers": {},
            },
        }
    )

    summary = recorder.stop(tmp_path / "network.har")

    assert summary["entry_count"] == 1
    assert summary["request_count"] == 1


def test_har_recorder_omits_response_bodies_the_evaluator_cannot_parse(tmp_path):
    context = _Context()
    page = _Page(context)
    context.pages.append(page)
    recorder = CdpHarRecorder(page)
    session = context.sessions[0]
    session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "html",
            "request": {
                "method": "GET",
                "url": "https://example.test/account",
                "headers": {},
            },
        }
    )
    session.handlers["Network.responseReceived"](
        {
            "requestId": "html",
            "response": {
                "status": 200,
                "headers": {"content-type": "text/html"},
                "mimeType": "text/html",
            },
        }
    )
    session.handlers["Network.loadingFinished"]({"requestId": "html"})
    output = tmp_path / "network.har"

    recorder.stop(output)

    content = json.loads(output.read_text())["log"]["entries"][0]["response"]["content"]
    assert content == {"size": len('{"ok":true}'), "mimeType": "text/html"}
    assert '{"ok":true}' not in output.read_text()
