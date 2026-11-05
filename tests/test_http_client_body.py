"""Public HTTP transport preserves binary uploads and existing JSON requests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from harness.web_fetch.providers import http_client


@pytest.fixture
def endpoint():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((dict(self.headers), raw))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}/upload", received
    server.shutdown()
    worker.join()
    server.server_close()


def test_binary_upload_arrives_unchanged_with_its_content_type(endpoint):
    url, received = endpoint
    raw = "<html>中文</html>".encode() + b"\r\n\x00\xff"
    response = http_client.request(
        url,
        method="POST",
        body=raw,
        headers={"Content-Type": "multipart/form-data; boundary=fixture"},
    )
    assert response.json() == {"ok": True}
    assert received[0][1] == raw
    assert received[0][0]["Content-Type"] == "multipart/form-data; boundary=fixture"


def test_json_request_contract_is_unchanged(endpoint):
    url, received = endpoint
    value = {"text": "中文", "list": [1, None]}
    assert http_client.request(url, method="POST", json_body=value).status == 200
    assert json.loads(received[0][1]) == value
    assert received[0][0]["Content-Type"] == "application/json"


@pytest.mark.parametrize(
    "body,json_body,error", [(b"bytes", {}, ValueError), ("text", None, TypeError)]
)
def test_invalid_body_configuration_never_opens_a_connection(monkeypatch, body, json_body, error):
    opener = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(http_client.urllib.request, "build_opener", opener)
    with pytest.raises(error):
        http_client.request("https://api.example/upload", body=body, json_body=json_body)
    opener.assert_not_called()
