"""Independent collectors share the container's real worker and cleanup budget."""

import json
import socket
import subprocess
import sys
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

    def worker(value, **kwargs):
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

    def worker(value, **kwargs):
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


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux request containment')
def test_http_parent_death_keeps_admission_until_all_owned_processes_exit(tmp_path):
    import psutil
    import requests

    pid_file = tmp_path / 'detached.pid'
    directory = tmp_path / 'admission'
    descendant = (
        'import os,time;from pathlib import Path;'
        f'Path({str(pid_file)!r}).write_text(str(os.getpid()),encoding="utf-8");time.sleep(60)'
    )
    native = (
        'import subprocess,sys,time;'
        f'subprocess.Popen([sys.executable,"-c",{descendant!r}],start_new_session=True);'
        'time.sleep(60)'
    )
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    helper = tmp_path / 'http-parent.py'
    helper.write_text(
        'import os,sys,uvicorn\nfrom pathlib import Path\nfrom types import SimpleNamespace\n'
        'from harness.web_extract import self_hosted as m\n'
        f'm.ADMISSION_DIRECTORY=Path({str(directory)!r})\n'
        'os.environ.update(EXTRACTION_API_KEY="service-key",EXTRACTION_MAX_WORKERS="1",'
        'SNAPSHOT_ORIGINS="https://snapshots.example")\n'
        'm.prepare_environment=lambda:(SimpleNamespace(cleanup=lambda:None),None)\n'
        f'm.runtime_identity=lambda engine:{IDENTITY!r}\n'
        'real=m.run_worker\n'
        f'def worker(value,**kwargs):return real(value,timeout=15,command='
        f'[sys.executable,"-c",{native!r}],**kwargs)\n'
        'm.run_worker=worker\n'
        f'uvicorn.run(m.create_app(),host="127.0.0.1",port={port},log_level="error")\n',
        encoding='utf-8',
    )
    parent = subprocess.Popen([sys.executable, str(helper)], start_new_session=True)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    owned = []
    request = None
    original_directory = self_hosted.ADMISSION_DIRECTORY
    try:
        origin = f'http://127.0.0.1:{port}'
        for _ in range(300):
            try:
                if requests.get(origin + '/health', timeout=.2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            assert parent.poll() is None, 'HTTP parent failed before the request'
            time.sleep(.01)
        else:
            raise AssertionError('HTTP parent never became ready')

        def send():
            try:
                requests.post(origin + '/extract/crawl4ai', json=BODY, headers=AUTH, timeout=20)
            except requests.RequestException:
                pass

        request = threading.Thread(target=send, daemon=True)
        request.start()
        for _ in range(300):
            if pid_file.exists():
                break
            time.sleep(.01)
        assert pid_file.exists(), 'Native descendant never started'
        owned = psutil.Process(parent.pid).children(recursive=True)
        assert len(owned) >= 3
        parent.kill()
        parent.wait(timeout=3)
        self_hosted.ADMISSION_DIRECTORY = directory
        for _ in range(500):
            alive = [p for p in owned if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
            lease = self_hosted.acquire_worker_slot(1)
            if lease is not None:
                lease.close()
                assert not alive, 'Worker admission reopened while orphaned extraction survived'
                break
            time.sleep(.01)
        else:
            raise AssertionError('Admission was not released after parent-death cleanup')
        assert unrelated.poll() is None
    finally:
        self_hosted.ADMISSION_DIRECTORY = original_directory
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        for process in reversed(owned):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        unrelated.kill()
        unrelated.wait(timeout=3)
        if request is not None:
            request.join(timeout=1)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux request containment')
def test_supervisor_enforces_its_deadline_without_an_http_timeout(tmp_path):
    import sys

    import psutil

    pid_file = tmp_path / 'deadline.pid'
    command = (
        'import os,time;from pathlib import Path;'
        f'Path({str(pid_file)!r}).write_text(str(os.getpid()),encoding="utf-8");time.sleep(60)'
    )
    code = (
        'import sys;from harness.web_extract.self_hosted import supervise;'
        f'sys.exit(supervise([sys.executable,"-c",{command!r}],timeout=.5))'
    )
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=5)
    assert result.returncode == 142
    assert pid_file.exists() and not psutil.pid_exists(int(pid_file.read_text(encoding='utf-8')))
