"""Exercise the real HTTP validation boundary without loading extractor SDKs."""

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
from fastapi.testclient import TestClient

from harness.web_extract.self_hosted import create_app, run_worker

IDENTITY = {"engine": "crawl4ai", "version": "synthetic"}
BODY = {
    "url": "https://snapshots.example/frozen/page",
    "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
    "expected_runtime": IDENTITY,
}
AUTH = {"Authorization": "Bearer test-service-key"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EXTRACTION_API_KEY", "test-service-key")
    monkeypatch.setenv("SNAPSHOT_ORIGINS", "https://snapshots.example")
    with patch("harness.web_extract.self_hosted.runtime_identity", return_value=IDENTITY):
        with TestClient(create_app()) as value:
            yield value


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        3,
        "text",
        {},
        {**BODY, "url": 123},
        {**BODY, "schema": {"type": "object", "properties": ["name"]}},
    ],
)
def test_malformed_shapes_are_client_errors_without_starting_a_worker(client, body):
    with patch("harness.web_extract.self_hosted.run_worker") as worker:
        response = client.post("/extract/crawl4ai", json=body, headers=AUTH)
    assert response.status_code == 400
    worker.assert_not_called()


@pytest.mark.parametrize("content", [b"{", b"\xff"])
def test_invalid_json_is_rejected_before_dispatch(client, content):
    with patch("harness.web_extract.self_hosted.run_worker") as worker:
        response = client.post("/extract/crawl4ai", content=content, headers=AUTH)
    assert response.status_code == 400
    worker.assert_not_called()


def test_chunked_body_cannot_bypass_the_size_limit(client):
    def chunks():
        yield b'{"padding":"' + b"a" * 60000
        yield b"a" * 60000 + b'"}'

    with patch("harness.web_extract.self_hosted.run_worker") as worker:
        response = client.post("/extract/crawl4ai", content=chunks(), headers=AUTH)
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    worker.assert_not_called()


@pytest.mark.parametrize("length", ["bad", "-1"])
def test_invalid_content_length_is_a_client_error(client, length):
    response = client.post(
        "/extract/crawl4ai", content=b"{}", headers={**AUTH, "Content-Length": length}
    )
    assert response.status_code == 400


def test_valid_request_and_native_failure_use_the_same_http_boundary(client):
    payload = {"success": True, "data": {"name": "Example"}, "runtime": IDENTITY}
    completed = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
    with patch("harness.web_extract.self_hosted.run_worker", return_value=completed) as worker:
        response = client.post("/extract/crawl4ai", json=BODY, headers=AUTH)
    assert response.status_code == 200 and response.json() == payload
    assert worker.call_args.args[0] == {**BODY, "engine": "crawl4ai"}
    with patch(
        "harness.web_extract.self_hosted.run_worker", side_effect=subprocess.TimeoutExpired([], 110)
    ):
        assert client.post("/extract/crawl4ai", json=BODY, headers=AUTH).status_code == 504


def test_timeout_terminates_a_browser_style_detached_child(tmp_path):
    pid_file = tmp_path / "browser.pid"
    script = tmp_path / "native.py"
    script.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    pid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_worker({}, timeout=1, command=[sys.executable, str(script)])
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not psutil.pid_exists(pid)
    finally:
        if pid is None and pid_file.exists():
            pid = int(pid_file.read_text(encoding="utf-8"))
        if pid and psutil.pid_exists(pid):
            psutil.Process(pid).kill()


def test_backend_attestation_changes_with_the_native_source_and_model(tmp_path, monkeypatch):
    import os
    import signal
    from contextlib import contextmanager

    from harness.web_extract.self_hosted import runtime_identity, validate_input

    bundle = tmp_path / "dist/src"
    bundle.mkdir(parents=True)
    script = bundle / "harness.js"
    script.write_text("setInterval(() => {}, 1000);\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/native.js").write_text("module.exports = 1;\n", encoding="utf-8")
    wrapper = Path(__file__).resolve().parents[2] / "src/harness/web_extract/firecrawl_backend.mjs"
    base = {"engine": "firecrawl", "model": "test-model", "model_base": "https://model.example"}
    monkeypatch.setenv("FIRECRAWL_BACKEND_URL", "http://private-backend.example:3002")
    monkeypatch.setenv("SNAPSHOT_ORIGINS", "https://snapshots.example")

    @contextmanager
    def backend(model):
        process = subprocess.Popen(
            ["node", str(wrapper)],
            cwd=tmp_path,
            env={
                **os.environ,
                "FIRECRAWL_IDENTITY_PORT": "0",
                "MODEL_NAME": model,
                "OPENAI_BASE_URL": base["model_base"],
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            line = process.stdout.readline()
            assert line, process.stderr.read()
            port = json.loads(line)["identity_port"]
            monkeypatch.setenv("FIRECRAWL_IDENTITY_URL", f"http://127.0.0.1:{port}/identity")
            with patch("harness.web_extract.self_hosted.local_runtime_identity", return_value=base):
                yield runtime_identity("firecrawl")
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=5)

    with backend("test-model") as original:
        assert original["backend"]["controls"]["MODEL_NAME"] == "test-model"
    script.write_text("setInterval(() => {}, 2000);\n", encoding="utf-8")
    with backend("test-model") as changed:
        assert (
            changed["backend"]["native_bundle_sha256"]
            != original["backend"]["native_bundle_sha256"]
        )
        with patch("harness.web_extract.self_hosted.local_runtime_identity", return_value=base):
            with pytest.raises(ValueError, match="frozen extraction configuration"):
                validate_input({**BODY, "engine": "firecrawl", "expected_runtime": original})
    with pytest.raises(ValueError, match="backend model differs"):
        with backend("different-model"):
            pass
