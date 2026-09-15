import base64
import json

from harness.browser_use.har import CdpHarRecorder, _har_request, _har_response


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

        def send(self, _method, _params):
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
