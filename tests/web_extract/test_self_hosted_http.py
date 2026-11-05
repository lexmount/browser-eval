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


@pytest.mark.parametrize("padding", [0, 5990, 7000])
def test_worker_errors_redact_credentials_before_the_public_length_limit(
    client, monkeypatch, padding
):
    secret = "synthetic-model-credential"
    monkeypatch.setenv("EXTRACTION_MODEL_API_KEY", secret)
    completed = subprocess.CompletedProcess([], 1, "", secret + "x" * padding)
    with patch("harness.web_extract.self_hosted.run_worker", return_value=completed):
        response = client.post("/extract/crawl4ai", json=BODY, headers=AUTH)
    error = response.json()["error"]
    assert response.status_code == 200 and len(error) <= 6000
    assert secret not in error and secret[-10:] not in error
    if padding == 0:
        assert error == "[REDACTED]"


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
        {**BODY, "schema": {"type": "object", "properties": {"name": 123}}},
        {**BODY, "schema": {"type": "object", "properties": {"name": {"type": "bogus"}}}},
        {**BODY, "instruction": 123},
        {**BODY, "instruction": None},
    ],
)
def test_malformed_shapes_are_client_errors_without_starting_a_worker(client, body):
    with patch("harness.web_extract.self_hosted.run_worker") as worker:
        response = client.post("/extract/crawl4ai", json=body, headers=AUTH)
    assert response.status_code == 400
    worker.assert_not_called()


@pytest.mark.parametrize("content", [b"{", b"\xff", b"[" * 2000 + b"0" + b"]" * 2000])
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

    external = tmp_path / "external-package"
    external.mkdir()
    target = external / "index.js"
    target.write_text("module.exports = 1;\n", encoding="utf-8")
    (external / "cycle").symlink_to(external, target_is_directory=True)
    (tmp_path / "node_modules/linked").symlink_to(external, target_is_directory=True)
    with backend("test-model") as linked:
        pass
    target.write_text("module.exports = 2;\n", encoding="utf-8")
    with backend("test-model") as changed_link:
        assert (
            linked["backend"]["native_dependencies_sha256"]
            != changed_link["backend"]["native_dependencies_sha256"]
        )

    first = tmp_path / "node_modules/a"
    first.write_bytes(b"xb\0file\0y")
    with backend("test-model") as single_file:
        pass
    first.write_bytes(b"x")
    (tmp_path / "node_modules/b").write_bytes(b"y")
    with backend("test-model") as two_files:
        assert (
            single_file["backend"]["native_dependencies_sha256"]
            != two_files["backend"]["native_dependencies_sha256"]
        )

    optional = tmp_path / "optional-package.js"
    (tmp_path / "node_modules/optional").symlink_to(optional)
    with backend("test-model") as missing_optional:
        pass
    optional.write_text("module.exports = 3;\n", encoding="utf-8")
    with backend("test-model") as installed_optional:
        assert (
            missing_optional["backend"]["native_dependencies_sha256"]
            != installed_optional["backend"]["native_dependencies_sha256"]
        )


@pytest.mark.parametrize(
    "failure",
    [
        "occupied-port",
        "openai-userinfo",
        "openai-query",
        "ollama-query",
        "malformed-openai",
        "malformed-ollama",
        "node-preload",
        "node-path",
    ],
)
def test_backend_rejects_unsafe_startup_without_leaving_a_native_child(tmp_path, failure):
    import os
    import signal
    import socket

    bundle = tmp_path / "dist/src"
    bundle.mkdir(parents=True)
    (tmp_path / "node_modules").mkdir()
    marker = tmp_path / "native-started"
    (bundle / "harness.js").write_text(
        f"require('fs').writeFileSync({json.dumps(str(marker))}, String(process.pid));"
        "setInterval(() => {}, 1000);",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "OPENAI_BASE_URL": "https://model.example",
        "OLLAMA_BASE_URL": "",
        "FIRECRAWL_IDENTITY_PORT": "0",
    }
    with socket.socket() as occupied:
        occupied.bind(("0.0.0.0", 0))
        occupied.listen()
        if failure == "occupied-port":
            env["FIRECRAWL_IDENTITY_PORT"] = str(occupied.getsockname()[1])
        elif failure == "openai-userinfo":
            env["OPENAI_BASE_URL"] = "https://user:credential@model.example"
        elif failure == "malformed-openai":
            env["OPENAI_BASE_URL"] = "https://user:private-secret-token@host:invalid"
        elif failure == "malformed-ollama":
            env["OLLAMA_BASE_URL"] = "https://user:private-secret-token@[invalid"
        elif failure == "node-preload":
            preload = tmp_path / "preload.cjs"
            preload.write_text("global.externalOverride = true;", encoding="utf-8")
            env["NODE_OPTIONS"] = f"--require={preload}"
        elif failure == "node-path":
            env["NODE_PATH"] = str(tmp_path / "external_modules")
        else:
            env["OLLAMA_BASE_URL" if failure == "ollama-query" else "OPENAI_BASE_URL"] = (
                "https://model.example?api_key=credential"
            )
        process = subprocess.Popen(
            [
                "node",
                str(
                    Path(__file__).resolve().parents[2]
                    / "src/harness/web_extract/firecrawl_backend.mjs"
                ),
            ],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode != 0 and "identity_port" not in stdout
            expected = (
                "EADDRINUSE"
                if failure == "occupied-port"
                else "credential-free HTTP(S) URL"
                if failure.startswith("malformed-")
                else "code-loading overrides are unsupported"
                if failure.startswith("node-")
                else "must not contain credentials"
            )
            assert expected in stderr and "private-secret-token" not in stderr
            assert not marker.exists()
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=2)


def test_slow_identity_validation_does_not_block_other_http_requests(monkeypatch):
    import asyncio
    import threading

    import httpx

    entered, release, expired = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setenv("EXTRACTION_API_KEY", "test-service-key")
    monkeypatch.setenv("SNAPSHOT_ORIGINS", "https://snapshots.example")
    monkeypatch.setenv("FIRECRAWL_BACKEND_URL", "http://private-backend.example")

    def identity(engine):
        if engine == "firecrawl":
            entered.set()
            if not release.wait(timeout=3):
                expired.set()
        return {**IDENTITY, "engine": engine}

    async def exercise():
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            slow = asyncio.create_task(
                client.post(
                    "/extract/firecrawl",
                    json={**BODY, "expected_runtime": identity_value},
                    headers=AUTH,
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 4)
                response = await client.post("/extract/crawl4ai", json=BODY, headers=AUTH)
                assert response.status_code == 200 and not expired.is_set()
            finally:
                release.set()
                await slow

    identity_value = {**IDENTITY, "engine": "firecrawl"}
    completed = subprocess.CompletedProcess([], 0, json.dumps({"success": True}), "")
    with (
        patch("harness.web_extract.self_hosted.runtime_identity", side_effect=identity),
        patch("harness.web_extract.self_hosted.run_worker", return_value=completed),
    ):
        asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux request subreaper containment")
def test_timeout_reaps_reparented_detached_processes_without_killing_other_requests(tmp_path):
    import os

    pid_file = tmp_path / "orphan.pid"
    orphan = (
        f"from pathlib import Path; import os,time; Path({str(pid_file)!r})"
        ".write_text(str(os.getpid()),encoding='utf-8'); time.sleep(60)"
    )
    intermediate = (
        f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{orphan!r}],"
        "start_new_session=True)"
    )
    script = tmp_path / "native.py"
    script.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.run([sys.executable,'-c',{intermediate!r}])\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    invocation = (
        "from harness.web_extract.self_hosted import run_worker; run_worker({},timeout=1,command="
        + repr([sys.executable, str(script)])
        + ")"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        result = subprocess.run(
            [sys.executable, "-c", invocation], capture_output=True, text=True, timeout=7
        )
        assert "TimeoutExpired" in result.stderr
        assert pid_file.exists()
        assert not psutil.pid_exists(int(pid_file.read_text(encoding="utf-8")))
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=2)
        if pid_file.exists() and psutil.pid_exists(int(pid_file.read_text(encoding="utf-8"))):
            os.kill(int(pid_file.read_text(encoding="utf-8")), 9)


@pytest.mark.parametrize(
    "base",
    [
        "https://user:credential@model.example",
        "https://model.example?api_key=credential",
        "https://model.example#credential",
    ],
)
def test_health_identity_rejects_credential_bearing_model_urls(monkeypatch, base):
    from harness.web_extract.self_hosted import local_runtime_identity

    local_runtime_identity.cache_clear()
    monkeypatch.setenv("EXTRACTION_MODEL_BASE", base)
    with pytest.raises(ValueError, match="must not contain credentials"):
        local_runtime_identity("crawl4ai")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux request subreaper containment")
def test_interruption_during_native_launch_still_cleans_up_the_spawned_process(tmp_path):
    import os

    pid_file = tmp_path / "launch.pid"
    native = (
        "import os,time; from pathlib import Path; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()),encoding='utf-8'); time.sleep(60)"
    )
    helper = tmp_path / "interrupted-launch.py"
    helper.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\nfrom unittest.mock import patch\n"
        "from harness.web_extract.self_hosted import supervise\nreal = subprocess.Popen\n"
        "def interrupted(*args, **kwargs):\n"
        "    real(*args, **kwargs)\n"
        "    deadline=time.monotonic()+2\n"
        f"    while not Path({str(pid_file)!r}).exists() and time.monotonic()<deadline:\n"
        "        time.sleep(0.01)\n"
        "    raise SystemExit(143)\n"
        "with patch('harness.web_extract.self_hosted.subprocess.Popen', side_effect=interrupted):\n"
        f"    supervise([sys.executable,'-c',{native!r}])\n",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [sys.executable, str(helper)], capture_output=True, text=True, timeout=7
        )
        assert result.returncode == 143 and pid_file.exists()
        assert not psutil.pid_exists(int(pid_file.read_text(encoding="utf-8")))
    finally:
        if pid_file.exists() and psutil.pid_exists(int(pid_file.read_text(encoding="utf-8"))):
            os.kill(int(pid_file.read_text(encoding="utf-8")), 9)
