import base64
import json

from harness.browser_use.har import CdpHarRecorder, _har_request, _har_response, _is_json_mime_type


def test_capture_explicitly_retains_navigation_json_with_finite_limits(tmp_path) -> None:
    class Session:
        def __init__(self):
            self.calls = []
            self.handlers = {}
            self.retained = False

        def on(self, event, handler):
            self.handlers[event] = handler

        def send(self, method, params=None):
            self.calls.append((method, params))
            if method == "Network.enable":
                self.retained = params.get("enableDurableMessages") is True
                assert 0 < params["maxResourceBufferSize"] <= params["maxTotalBufferSize"]
                assert params["maxTotalBufferSize"] == 10 * 1024 * 1024
                assert params["maxResourceBufferSize"] == 1024 * 1024
            if method == "Network.getResponseBody":
                if not self.retained:
                    raise RuntimeError("body cleared during navigation")
                return {"body": '{"saved":true}', "base64Encoded": False}
            return {}

        def detach(self):
            self.retained = False

    session = Session()

    class Context:
        pages = [object()]

        def on(self, *_args):
            pass

        def remove_listener(self, *_args):
            pass

        def new_cdp_session(self, _page):
            return session

    context = Context()
    recorder = CdpHarRecorder()
    recorder.start(context)
    session.handlers["Network.requestWillBeSent"](
        {
            "requestId": "save",
            "type": "XHR",
            "request": {"method": "POST", "url": "http://example.test/save"},
        }
    )
    session.handlers["Network.responseReceived"](
        {
            "requestId": "save",
            "response": {"status": 200, "mimeType": "application/json"},
        }
    )
    session.handlers["Network.loadingFinished"]({"requestId": "save"})
    path = tmp_path / "durable.har"
    recorder.stop(context, path)
    assert recorder.errors == []
    assert (
        json.loads(path.read_text())["log"]["entries"][0]["response"]["content"]["text"]
        == '{"saved":true}'
    )
    assert [method for method, _ in session.calls].count("Network.enable") == 1
    assert [method for method, _ in session.calls].count("Page.getFrameTree") == 2
    assert session.retained is False


def test_har_preserves_multipart_post_data_and_json_response() -> None:
    request = _har_request(
        {
            "method": "POST",
            "url": "http://example.test/vote",
            "headers": {"Content-Type": "multipart/form-data; boundary=test"},
            "postData": '--test\r\nContent-Disposition: form-data; name="choice"\r\n\r\n1',
        }
    )
    response = _har_response(
        {
            "status": 200,
            "statusText": "OK",
            "mimeType": "application/json",
            "headers": {},
        },
        {"body": '{"items_qty":1}', "base64Encoded": False},
    )

    assert request["postData"]["mimeType"] == "multipart/form-data; boundary=test"
    assert 'name="choice"' in request["postData"]["text"]
    assert response["content"]["text"] == '{"items_qty":1}'


def test_har_preserves_only_evaluator_safe_response_cookie_from_extra_info() -> None:
    response = _har_response(
        {"status": 302, "headers": {"Location": "/wishlist"}},
        None,
        {
            "Set-Cookie": (
                "PHPSESSID=secret; Path=/\r\n"
                "mage-messages=product%20added; Path=/"
            )
        },
    )

    assert response["cookies"] == [
        {"name": "mage-messages", "value": "product%20added"}
    ]
    assert "secret" not in json.dumps(response)


def test_json_mime_matching_uses_case_insensitive_essence() -> None:
    for value in (
        "application/json",
        "application/json; charset=utf-8",
        " Application/JSON ; Charset=UTF-8 ",
        "application/problem+json",
        "Application/Vnd.Example+JSON ; version=2",
    ):
        assert _is_json_mime_type(value), value
    for value in (
        "",
        "+json",
        "text/json",
        "application/jsonp",
        "application/json-seq",
        "text/plain",
    ):
        assert not _is_json_mime_type(value), value


def test_parameterized_json_body_is_captured_byte_exactly(tmp_path) -> None:
    expected = '{"name":"Gülçek","literal":"<b>&amp;</b>"}'

    class Session:
        def send(self, method, _params=None):
            if method == "Network.getResponseBody":
                return {"body": expected, "base64Encoded": False}
            return {}

        def detach(self):
            pass

    session = Session()
    recorder = CdpHarRecorder()
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        id(session),
        {
            "requestId": "json",
            "request": {"method": "GET", "url": "http://example.test/data"},
        },
    )
    recorder._record_response(
        session,
        id(session),
        {
            "requestId": "json",
            "response": {
                "status": 200,
                "mimeType": " Application/Problem+JSON ; Charset=UTF-8 ",
                "headers": {},
            },
        },
    )
    recorder._record_finished(session, id(session), {"requestId": "json"})
    path = tmp_path / "parameterized-json.har"

    recorder.stop(object(), path)

    content = json.loads(path.read_text())["log"]["entries"][0]["response"]["content"]
    assert content["text"] == expected


def test_recorder_retains_redirect_response_before_request_id_is_reused() -> None:
    recorder = CdpHarRecorder()
    session = object()
    recorder._record_request(
        session,
        7,
        {"requestId": "1", "request": {"method": "POST", "url": "http://x/edit"}},
    )
    recorder._record_request(
        session,
        7,
        {
            "requestId": "1",
            "redirectResponse": {"status": 302},
            "request": {"method": "GET", "url": "http://x/profile"},
        },
    )

    assert recorder._responses[0][2]["method"] == "POST"
    assert recorder._responses[0][3]["status"] == 302
    assert recorder._requests[(7, "1")]["method"] == "GET"


def test_document_request_has_navigation_accept_fallback() -> None:
    request = _har_request(
        {
            "method": "GET",
            "url": "http://example.test/page",
            "headers": {},
            "__resource_type": "Document",
        }
    )

    assert {item["name"]: item["value"] for item in request["headers"]}["Accept"] == "text/html"


def test_stop_is_idempotent_and_does_not_overwrite_har(tmp_path) -> None:
    recorder = CdpHarRecorder()
    path = tmp_path / "network.har"
    recorder.stop(object(), path)
    first = path.read_text()
    path.write_text("sentinel")

    recorder.stop(object(), path)

    assert first != "sentinel"
    assert path.read_text() == "sentinel"


def test_stop_serializes_one_response_snapshot_when_callbacks_append(tmp_path) -> None:
    class AppendOnSecondIteration(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            yield from super().__iter__()
            if self.iterations == 2:
                yield (
                    object(),
                    "late",
                    {"method": "GET", "url": "http://example.test/late", "headers": {}},
                    {"status": 200, "mimeType": "text/html", "headers": {}},
                )

    session = object()
    recorder = CdpHarRecorder()
    recorder._responses = AppendOnSecondIteration(
        [
            (
                session,
                "1",
                {"method": "GET", "url": "http://example.test/first", "headers": {}},
                {"status": 200, "mimeType": "text/html", "headers": {}},
            )
        ]
    )
    path = tmp_path / "network.har"

    recorder.stop(object(), path)

    entries = json.loads(path.read_text())["log"]["entries"]
    assert [entry["request"]["url"] for entry in entries] == ["http://example.test/first"]


def test_stop_decodes_base64_json_for_official_har_parser(tmp_path) -> None:
    class Session:
        def send(self, _method, _params):
            return {
                "body": base64.b64encode(b'{"items_qty":1}').decode(),
                "base64Encoded": True,
            }

        def detach(self):
            pass

    session = Session()
    recorder = CdpHarRecorder()
    recorder._sessions.append(session)
    recorder._finished.add((id(session), "1"))
    recorder._responses.append(
        (
            session,
            "1",
            {"method": "GET", "url": "http://example.test/totals", "headers": {}},
            {"status": 200, "mimeType": "application/json", "headers": {}},
        )
    )
    path = tmp_path / "network.har"

    recorder.stop(object(), path)

    content = json.loads(path.read_text())["log"]["entries"][0]["response"]["content"]
    assert content == {"mimeType": "application/json", "text": '{"items_qty":1}'}


def test_recorder_captures_json_body_when_loading_finishes(tmp_path) -> None:
    class Session:
        calls = 0

        def send(self, method, _params):
            if method == "Page.getFrameTree":
                return {}
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("body already evicted")
            return {"body": '{"items_qty":1}', "base64Encoded": False}

        def detach(self):
            pass

    session = Session()
    recorder = CdpHarRecorder()
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        id(session),
        {
            "requestId": "1",
            "request": {"method": "GET", "url": "http://example.test/totals"},
        },
    )
    recorder._record_response(
        session,
        id(session),
        {
            "requestId": "1",
            "response": {"status": 200, "mimeType": "application/json", "headers": {}},
        },
    )

    recorder._record_finished(session, id(session), {"requestId": "1"})
    path = tmp_path / "network.har"
    recorder.stop(object(), path)

    content = json.loads(path.read_text())["log"]["entries"][0]["response"]["content"]
    assert content["text"] == '{"items_qty":1}'
    assert session.calls == 1
    assert recorder.errors == []


def test_invalid_base64_is_not_written_as_plain_json(tmp_path) -> None:
    class Session:
        def send(self, _method, _params):
            return {"body": "not valid base64!", "base64Encoded": True}

        def detach(self):
            pass

    session = Session()
    recorder = CdpHarRecorder()
    recorder._sessions.append(session)
    recorder._finished.add((id(session), "1"))
    recorder._responses.append(
        (
            session,
            "1",
            {"method": "GET", "url": "http://example.test/totals", "headers": {}},
            {"status": 200, "mimeType": "application/json", "headers": {}},
        )
    )
    path = tmp_path / "network.har"

    recorder.stop(object(), path)

    content = json.loads(path.read_text())["log"]["entries"][0]["response"]["content"]
    assert content["text"] == ""
    assert recorder.errors


def test_timing_and_redirect_chain_preserve_official_evaluation_events(tmp_path) -> None:
    from webarena_verified.types.tracing import NetworkTrace

    recorder = CdpHarRecorder()
    session = object()
    key = id(session)
    recorder._record_request(
        session,
        key,
        {
            "requestId": "same",
            "timestamp": 10.0,
            "wallTime": 1700000000.25,
            "request": {
                "method": "POST",
                "url": "http://example.test/save",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                "postData": "value=1",
            },
        },
    )
    recorder._record_request(
        session,
        key,
        {
            "requestId": "same",
            "timestamp": 10.2,
            "wallTime": 1700000000.45,
            "redirectResponse": {
                "status": 302,
                "headers": {"Location": "/done"},
                "timing": {"requestTime": 10.0, "receiveHeadersEnd": 180},
            },
            "request": {"method": "GET", "url": "http://example.test/done"},
        },
    )
    recorder._record_response(
        session,
        key,
        {
            "requestId": "same",
            "timestamp": 10.3,
            "response": {"status": 200, "mimeType": "text/html", "responseTime": 1700000000550},
        },
    )
    recorder._record_finished(session, key, {"requestId": "same", "timestamp": 10.4})
    path = tmp_path / "timed.har"
    recorder.stop(object(), path)
    payload = json.loads(path.read_text())
    entries = payload["log"]["entries"]
    first, second = entries
    assert first["startedDateTime"] == "2023-11-14T22:13:20.250000Z"
    assert first["_cdp"] == {
        "sessionOrdinal": 0,
        "requestId": "same",
        "redirectOrdinal": 0,
        "requestTimestamp": 10.0,
        "requestWallTime": 1700000000.25,
        "responseSource": "requestWillBeSent.redirectResponse",
        "redirectObservedTimestamp": 10.2,
        "responseTiming": {"requestTime": 10.0, "receiveHeadersEnd": 180},
    }
    assert second["_cdp"]["redirectOrdinal"] == 1
    assert second["_cdp"]["responseReceivedTimestamp"] == 10.3
    assert second["_cdp"]["loadingFinishedTimestamp"] == 10.4
    assert second["_cdp"]["responseTime"] == 1700000000550
    assert "time" not in first  # no invented redirect completion duration
    original = NetworkTrace.from_har(path)
    payload["log"]["entries"] = [
        {"request": e["request"], "response": e["response"]} for e in entries
    ]
    plain = tmp_path / "plain.har"
    plain.write_text(json.dumps(payload))
    prior = NetworkTrace.from_har(plain)
    for old, new in zip(prior.events, original.events, strict=True):
        assert (
            old.url,
            old.http_method,
            old.post_data,
            old.response_content,
            old.is_evaluation_event,
        ) == (
            new.url,
            new.http_method,
            new.post_data,
            new.response_content,
            new.is_evaluation_event,
        )


def test_missing_timestamps_remain_missing_and_sessions_do_not_alias(tmp_path) -> None:
    recorder = CdpHarRecorder()
    sessions = [object(), object()]
    for session in sessions:
        recorder._record_request(
            session,
            id(session),
            {
                "requestId": "1",
                "request": {"method": "GET", "url": "http://example.test/"},
            },
        )
        recorder._record_response(
            session, id(session), {"requestId": "1", "response": {"status": 200}}
        )
    path = tmp_path / "network.har"
    recorder.stop(object(), path)
    entries = json.loads(path.read_text())["log"]["entries"]
    assert [entry["_cdp"]["sessionOrdinal"] for entry in entries] == [0, 1]
    for entry in entries:
        assert "startedDateTime" not in entry
        assert not any("Timestamp" in key or "Time" in key for key in entry["_cdp"])


def test_stop_barrier_delivers_queued_request_before_snapshot(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        armed = True

        def send(self, method, _params=None):
            if method == "Page.getFrameTree" and self.armed:
                self.armed = False
                recorder._record_request(
                    self,
                    id(self),
                    {
                        "requestId": "queued",
                        "type": "XHR",
                        "request": {
                            "method": "POST",
                            "url": "http://example.test/save",
                            "postData": "value=1",
                        },
                    },
                )
            return {}

        def detach(self):
            pass

    session = Session()
    recorder._sessions.append(session)
    path = tmp_path / "queued.har"

    recorder.stop(object(), path)

    entry = json.loads(path.read_text())["log"]["entries"][0]
    assert entry["request"]["method"] == "POST"
    assert entry["response"]["status"] == 0
    assert entry["_capture"] == {
        "state": "incomplete",
        "reason": "response-not-terminal-at-finalization",
    }


def test_stop_retains_request_only_document_without_fabricating_success(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, _method, _params=None):
            return {}

        def detach(self):
            pass

    session = Session()
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        id(session),
        {
            "requestId": "document",
            "type": "Document",
            "request": {"method": "POST", "url": "http://example.test/save"},
        },
    )
    path = tmp_path / "request-only.har"

    recorder.stop(object(), path)

    entry = json.loads(path.read_text())["log"]["entries"][0]
    assert entry["request"]["url"] == "http://example.test/save"
    assert entry["response"]["status"] == 0
    assert entry["_capture"]["state"] == "incomplete"


def test_stop_retains_pending_redirect_target_with_its_ordinal(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, _method, _params=None):
            return {}

        def detach(self):
            pass

    session = Session()
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        id(session),
        {
            "requestId": "same",
            "type": "Document",
            "request": {"method": "POST", "url": "http://example.test/save"},
        },
    )
    recorder._record_request(
        session,
        id(session),
        {
            "requestId": "same",
            "type": "Document",
            "redirectResponse": {"status": 302, "headers": {"Location": "/done"}},
            "request": {"method": "GET", "url": "http://example.test/done"},
        },
    )
    path = tmp_path / "redirect-pending.har"

    recorder.stop(object(), path)

    first, second = json.loads(path.read_text())["log"]["entries"]
    assert (first["request"]["method"], first["response"]["status"]) == ("POST", 302)
    assert (second["request"]["method"], second["response"]["status"]) == ("GET", 0)
    assert second["_cdp"]["redirectOrdinal"] == 1
    assert "_capture" not in first
    assert second["_capture"]["state"] == "incomplete"


def test_stop_drains_only_an_observed_slow_document_navigation(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, _method, _params=None):
            return {}

        def detach(self):
            pass

    session = Session()
    key = id(session)
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        key,
        {
            "requestId": "slow",
            "type": "Document",
            "request": {"method": "POST", "url": "http://example.test/save"},
        },
    )

    class Page:
        calls = 0

        def wait_for_timeout(self, timeout):
            assert 0 < timeout <= 50
            self.calls += 1
            # This request starts after the finalization boundary and is not a
            # continuation of the already-observed document navigation.
            recorder._record_request(
                session,
                key,
                {
                    "requestId": "late-metrics",
                    "type": "Fetch",
                    "request": {"method": "POST", "url": "http://example.test/metrics"},
                },
            )
            recorder._record_response(
                session,
                key,
                {
                    "requestId": "slow",
                    "response": {"status": 302, "headers": {"Location": "/done"}},
                },
            )
            recorder._record_finished(session, key, {"requestId": "slow"})

    page = Page()
    recorder._session_pages[key] = page
    path = tmp_path / "slow-document.har"

    recorder.stop(object(), path)

    entry = json.loads(path.read_text())["log"]["entries"][0]
    assert page.calls == 1
    assert entry["response"]["status"] == 302
    assert "_capture" not in entry
    assert [item["request"]["url"] for item in json.loads(path.read_text())["log"]["entries"]] == [
        "http://example.test/save"
    ]


def test_stop_does_not_drain_unfinished_subresource_or_idle_page(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Page:
        def wait_for_timeout(self, *_args, **_kwargs):
            raise AssertionError("subresources and idle pages must not delay finalization")

    class Session:
        barriers = 0

        def send(self, method, _params=None):
            assert method == "Page.getFrameTree"
            self.barriers += 1
            return {}

        def detach(self):
            pass

    idle = Session()
    subresource = Session()
    recorder._sessions.extend([idle, subresource])
    recorder._session_pages.update({id(idle): Page(), id(subresource): Page()})
    recorder._record_request(
        subresource,
        id(subresource),
        {
            "requestId": "stream",
            "type": "Fetch",
            "request": {"method": "GET", "url": "http://example.test/events"},
        },
    )
    path = tmp_path / "subresource.har"

    recorder.stop(object(), path)

    entries = json.loads(path.read_text())["log"]["entries"]
    assert idle.barriers == 2
    assert subresource.barriers == 2
    assert entries[0]["request"]["url"] == "http://example.test/events"
    assert entries[0]["response"]["status"] == 0


def test_stop_drains_iframe_document_by_request_terminal_event(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, _method, _params=None):
            return {}

        def detach(self):
            pass

    session = Session()
    key = id(session)
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        key,
        {
            "requestId": "iframe",
            "frameId": "child-frame",
            "type": "Document",
            "request": {"method": "GET", "url": "http://example.test/frame"},
        },
    )

    class Page:
        calls = 0

        def wait_for_timeout(self, timeout):
            assert 0 < timeout <= 50
            self.calls += 1
            recorder._record_response(
                session,
                key,
                {"requestId": "iframe", "response": {"status": 200, "mimeType": "text/html"}},
            )
            recorder._record_finished(session, key, {"requestId": "iframe"})

    page = Page()
    recorder._session_pages[key] = page
    path = tmp_path / "iframe.har"

    recorder.stop(object(), path)

    entry = json.loads(path.read_text())["log"]["entries"][0]
    assert page.calls == 1
    assert entry["response"]["status"] == 200
    assert "_capture" not in entry


def test_stop_ends_document_drain_on_loading_failed(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, _method, _params=None):
            return {}

        def detach(self):
            pass

    session = Session()
    key = id(session)
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        key,
        {
            "requestId": "failed",
            "type": "Document",
            "request": {"method": "POST", "url": "http://example.test/save"},
        },
    )

    class Page:
        calls = 0

        def wait_for_timeout(self, timeout):
            assert 0 < timeout <= 50
            self.calls += 1
            recorder._record_failed(key, {"requestId": "failed", "errorText": "net::ERR_ABORTED"})

    page = Page()
    recorder._session_pages[key] = page
    path = tmp_path / "failed.har"

    recorder.stop(object(), path)

    entry = json.loads(path.read_text())["log"]["entries"][0]
    assert page.calls == 1
    assert entry["response"]["status"] == 0
    assert entry["_capture"] == {"state": "failed", "errorText": "net::ERR_ABORTED"}


def test_redirect_json_body_is_scoped_to_the_final_ordinal(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, method, _params=None):
            if method == "Network.getResponseBody":
                return {"body": '{"final":true}', "base64Encoded": False}
            return {}

        def detach(self):
            pass

    session = Session()
    key = id(session)
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        key,
        {
            "requestId": "same",
            "type": "Document",
            "request": {"method": "POST", "url": "http://example.test/save"},
        },
    )
    recorder._record_request(
        session,
        key,
        {
            "requestId": "same",
            "type": "Document",
            "redirectResponse": {
                "status": 302,
                "mimeType": "application/json",
                "headers": {"Location": "/done"},
            },
            "request": {"method": "GET", "url": "http://example.test/done"},
        },
    )
    recorder._record_response(
        session,
        key,
        {
            "requestId": "same",
            "response": {"status": 200, "mimeType": "application/json", "headers": {}},
        },
    )
    recorder._record_finished(session, key, {"requestId": "same"})
    path = tmp_path / "redirect-json.har"

    recorder.stop(object(), path)

    first, second = json.loads(path.read_text())["log"]["entries"]
    assert first["response"]["content"]["text"] == ""
    assert second["response"]["content"]["text"] == '{"final":true}'


def test_request_order_places_older_pending_document_before_newer_completed_one(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, _method, _params=None):
            return {}

        def detach(self):
            pass

    session = Session()
    key = id(session)
    recorder._sessions.append(session)
    for request_id, url in (("old", "/old"), ("new", "/new")):
        recorder._record_request(
            session,
            key,
            {
                "requestId": request_id,
                "type": "Document",
                "request": {"method": "POST", "url": f"http://example.test{url}"},
            },
        )
    recorder._record_response(
        session,
        key,
        {"requestId": "new", "response": {"status": 200, "mimeType": "text/html"}},
    )
    recorder._record_finished(session, key, {"requestId": "new"})
    path = tmp_path / "pending-before-complete.har"

    recorder.stop(object(), path)

    entries = json.loads(path.read_text())["log"]["entries"]
    assert [entry["request"]["url"] for entry in entries] == [
        "http://example.test/old",
        "http://example.test/new",
    ]
    assert [entry["response"]["status"] for entry in entries] == [0, 200]


def test_request_order_is_stable_when_completed_responses_arrive_in_reverse(tmp_path) -> None:
    recorder = CdpHarRecorder()
    session = object()
    key = id(session)
    for request_id in ("first", "second"):
        recorder._record_request(
            session,
            key,
            {
                "requestId": request_id,
                "type": "Fetch",
                "request": {"method": "GET", "url": f"http://example.test/{request_id}"},
            },
        )
    for request_id in ("second", "first"):
        recorder._record_response(
            session,
            key,
            {"requestId": request_id, "response": {"status": 200, "mimeType": "text/plain"}},
        )
        recorder._record_finished(session, key, {"requestId": request_id})
    path = tmp_path / "reverse-responses.har"

    recorder.stop(object(), path)

    entries = json.loads(path.read_text())["log"]["entries"]
    assert [entry["request"]["url"] for entry in entries] == [
        "http://example.test/first",
        "http://example.test/second",
    ]


def test_redirect_chain_uses_request_order_when_responses_interleave(tmp_path) -> None:
    recorder = CdpHarRecorder()
    session = object()
    key = id(session)
    recorder._record_request(
        session,
        key,
        {
            "requestId": "redirect",
            "type": "Document",
            "request": {"method": "POST", "url": "http://example.test/save"},
        },
    )
    recorder._record_request(
        session,
        key,
        {
            "requestId": "redirect",
            "type": "Document",
            "redirectResponse": {"status": 302, "headers": {"Location": "/done"}},
            "request": {"method": "GET", "url": "http://example.test/done"},
        },
    )
    recorder._record_request(
        session,
        key,
        {
            "requestId": "later",
            "type": "Fetch",
            "request": {"method": "GET", "url": "http://example.test/later"},
        },
    )
    recorder._record_response(
        session,
        key,
        {"requestId": "later", "response": {"status": 200, "mimeType": "text/plain"}},
    )
    recorder._record_response(
        session,
        key,
        {"requestId": "redirect", "response": {"status": 200, "mimeType": "text/html"}},
    )
    path = tmp_path / "interleaved-redirect.har"

    recorder.stop(object(), path)

    entries = json.loads(path.read_text())["log"]["entries"]
    assert [entry["request"]["url"] for entry in entries] == [
        "http://example.test/save",
        "http://example.test/done",
        "http://example.test/later",
    ]
    assert [entry["response"]["status"] for entry in entries] == [302, 200, 200]


def test_body_fallback_callbacks_do_not_mutate_frozen_lifecycle_snapshot(tmp_path) -> None:
    recorder = CdpHarRecorder()

    class Session:
        def send(self, method, _params=None):
            if method == "Network.getResponseBody":
                recorder._record_response(
                    self,
                    id(self),
                    {
                        "requestId": "pending",
                        "response": {"status": 204, "mimeType": "text/plain"},
                    },
                )
                recorder._record_finished(self, id(self), {"requestId": "pending"})
                return {"body": '{"ok":true}', "base64Encoded": False}
            return {}

        def detach(self):
            pass

    session = Session()
    key = id(session)
    recorder._sessions.append(session)
    recorder._record_request(
        session,
        key,
        {
            "requestId": "json",
            "type": "Fetch",
            "request": {"method": "GET", "url": "http://example.test/json"},
        },
    )
    recorder._record_response(
        session,
        key,
        {
            "requestId": "json",
            "response": {"status": 200, "mimeType": "application/json"},
        },
    )
    recorder._finished.add((key, "json"))
    recorder._record_request(
        session,
        key,
        {
            "requestId": "pending",
            "type": "Fetch",
            "request": {"method": "POST", "url": "http://example.test/pending"},
        },
    )
    path = tmp_path / "frozen-lifecycle.har"

    recorder.stop(object(), path)

    first, second = json.loads(path.read_text())["log"]["entries"]
    assert first["response"]["content"]["text"] == '{"ok":true}'
    assert second["response"]["status"] == 0
    assert second["_capture"]["state"] == "incomplete"
