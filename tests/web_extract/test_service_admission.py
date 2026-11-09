"""Independent collectors share the container's real worker and cleanup budget."""

import json
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from harness.web_extract import self_hosted

IDENTITY = {"engine": "crawl4ai", "version": "synthetic"}
AUTH = {"Authorization": "Bearer service-key"}
BODY = {
    "url": "https://snapshots.example/page",
    "expected_runtime": IDENTITY,
    "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(self_hosted, "ADMISSION_DIRECTORY", tmp_path / "admission")
    monkeypatch.setenv("EXTRACTION_API_KEY", "service-key")
    monkeypatch.setenv("SNAPSHOT_ORIGINS", "https://snapshots.example")
    monkeypatch.setenv("EXTRACTION_MAX_WORKERS", "1")
    with (
        patch.object(self_hosted, "runtime_identity", return_value=IDENTITY),
        patch.object(
            self_hosted,
            "prepare_environment",
            return_value=(SimpleNamespace(cleanup=lambda: None), None),
        ),
    ):
        yield self_hosted.create_app


@pytest.mark.parametrize("capacity", [1, 2])
def test_separate_apps_share_one_worker_limit_and_release_after_cleanup(
    service, monkeypatch, capacity
):
    monkeypatch.setenv("EXTRACTION_MAX_WORKERS", str(capacity))
    ready = threading.Barrier(capacity + 1, timeout=5)
    release = threading.Event()
    count = []

    def worker(value):
        count.append(value)
        if len(count) <= capacity:
            ready.wait()
            assert release.wait(5)
        return subprocess.CompletedProcess([], 0, json.dumps({"success": True, "data": {}}), "")

    with ExitStack() as stack, patch.object(self_hosted, "run_worker", side_effect=worker):
        clients = [stack.enter_context(TestClient(service())) for _ in range(capacity + 1)]
        with ThreadPoolExecutor(capacity) as pool:
            futures = [
                pool.submit(c.post, "/extract/crawl4ai", json=BODY, headers=AUTH)
                for c in clients[:capacity]
            ]
            try:
                ready.wait()
                rejected = clients[-1].post("/extract/crawl4ai", json=BODY, headers=AUTH)
                assert rejected.status_code == 429 and len(count) == capacity
            finally:
                release.set()
            assert all(f.result(5).json()["success"] for f in futures)
        accepted = clients[-1].post("/extract/crawl4ai", json=BODY, headers=AUTH)
        assert accepted.status_code == 200 and len(count) == capacity + 1


def test_disconnect_does_not_release_a_still_running_worker(service):
    import requests
    import uvicorn

    started, release = threading.Event(), threading.Event()
    calls = []

    def worker(value):
        calls.append(value)
        started.set()
        assert release.wait(5)
        return subprocess.CompletedProcess([], 0, '{"success":true,"data":{}}', "")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(service(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    with patch.object(self_hosted, "run_worker", side_effect=worker):
        thread.start()
        try:
            deadline = time.monotonic() + 3
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.005)
            assert server.started
            first = requests.post(
                origin + "/extract/crawl4ai?keepalive=true",
                json=BODY,
                headers=AUTH,
                stream=True,
                timeout=3,
            )
            assert first.raw.read(1) == b"\n" and started.wait(2)
            first.close()
            second = requests.post(origin + "/extract/crawl4ai", json=BODY, headers=AUTH, timeout=2)
            assert second.status_code == 429 and len(calls) == 1
            release.set()
            time.sleep(0.03)
            third = requests.post(origin + "/extract/crawl4ai", json=BODY, headers=AUTH, timeout=2)
            assert third.status_code == 200 and len(calls) == 2
        finally:
            release.set()
            server.should_exit = True
            thread.join(5)
            listener.close()
    assert not thread.is_alive()
