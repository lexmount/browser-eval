"""Interrupted, denied and resumed native API collection scenarios."""

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_scraper.certification import frozen_probe
from harness.web_scraper.certification import run as certified_run
from harness.web_scraper.example import create
from harness.web_scraper.pipeline import access_blocker, digest, load_run, probe, save_json
from harness.web_scraper.stagehand import extract


def collection_context():
    stack = ExitStack()
    stack.enter_context(patch("harness.web_scraper.pipeline.validate_dataset"))
    stack.enter_context(patch("harness.web_scraper.pipeline.load_runtime_env"))
    stack.enter_context(
        patch.dict(
            "os.environ",
            {"CLOUDFLARE_ACCOUNT_ID": "synthetic", "CLOUDFLARE_API_TOKEN": "synthetic"},
        )
    )
    return stack


def accepted(*args, **kwargs):
    return {
        "state": "response_received",
        "http_status": 200,
        "response": {"result": {"sku": "value"}},
    }


def test_interrupted_inflight_request_is_not_silently_replayed(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/interrupted"
    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=KeyboardInterrupt) as call,
    ):
        with pytest.raises(KeyboardInterrupt):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert call.call_count == 1
    assert (target / "inflight.json").exists()
    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=accepted) as call,
    ):
        with pytest.raises(ValueError, match="Ambiguous in-flight"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        call.assert_not_called()


@pytest.mark.parametrize("entry", ["plan", "run"])
def test_repository_destination_is_rejected_before_any_receipt_write(tmp_path, entry):
    from harness.web_scraper.certification import create_plan

    root = tmp_path / "repository"
    root.mkdir()
    target = root / "private-evidence"
    with patch("harness.web_scraper.pipeline.ROOT", root):
        with pytest.raises(ValueError, match="outside the source repository"):
            if entry == "plan":
                create_plan(tmp_path, ["cloudflare-json"], target)
            else:
                certified_run(tmp_path, tmp_path / "absent-plan", "cloudflare-json", target)
    assert not target.exists()


@pytest.mark.parametrize(
    "message,kind",
    [
        ("Invalid API key", "authentication"),
        ("Invalid token", "authentication"),
        ("Permission denied", "permission"),
    ],
)
def test_native_authentication_envelopes_pause_but_target_errors_do_not(message, kind):
    native = {"http_status": 200, "response": {"success": False, "error": message}}
    target = {"http_status": 200, "response": {"statusCode": 403, "content": message}}
    assert access_blocker(native) == kind
    assert access_blocker(target) is None


def test_interrupted_finalization_resumes_without_repeating_provider_calls(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/finalizing"

    def save(path, value, **kwargs):
        if path.name == "manifest.json":
            raise KeyboardInterrupt
        return save_json(path, value, **kwargs)

    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=accepted) as call,
        patch("harness.web_scraper.pipeline.save_json", side_effect=save),
    ):
        with pytest.raises(KeyboardInterrupt):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert call.call_count == 3
    assert (target / "remote-fixture-after.json").exists()
    with collection_context(), patch("harness.web_scraper.pipeline.provider_call") as call:
        probe(home, ["cloudflare-json"], full=True, run_dir=target)
        call.assert_not_called()
    assert len(load_run(target)[1]) == 3


@pytest.mark.parametrize(
    "field,value",
    [("timeout_seconds", 60), ("quality_retries", 1), ("access_retries_after_repair", 0)],
)
def test_unsupported_frozen_policy_fails_before_collection(tmp_path, field, value):
    profile = tmp_path / "plan.json"
    plan = {
        "participants": {"cloudflare-json": {}},
        "timeout_seconds": 120,
        "quality_retries": 0,
        "access_retries_after_repair": 1,
    }
    plan[field] = value
    save_json(profile, plan)
    with patch("harness.web_scraper.certification.probe") as call:
        with pytest.raises(ValueError, match="Unsupported frozen"):
            certified_run(tmp_path, profile, "cloudflare-json", tmp_path / "run")
        call.assert_not_called()


def test_existing_unbound_execution_cannot_be_certified_after_collection(tmp_path):
    profile = tmp_path / "plan.json"
    save_json(
        profile,
        {
            "participants": {"cloudflare-json": {}},
            "timeout_seconds": 120,
            "quality_retries": 0,
            "access_retries_after_repair": 1,
        },
    )
    target = tmp_path / "run"
    save_json(target / "contract.json", {"already": "collected"})
    with pytest.raises(ValueError, match="Pre-call receipt is absent"):
        certified_run(tmp_path, profile, "cloudflare-json", target)
    assert not (target / "certification-precall.json").exists()


def test_certified_preflight_failure_can_resume_without_replacing_receipt(tmp_path):
    from harness.web_scraper.certification import create_plan
    from harness.web_scraper.pipeline import read_json

    home = create(tmp_path / "example")
    profile, target = tmp_path / "plan.json", home / "runs/preflight"
    with patch("harness.web_scraper.certification.validate_dataset"):
        create_plan(home, ["cloudflare-json"], profile)
    with (
        collection_context(),
        patch("harness.web_scraper.certification.validate_dataset"),
        patch(
            "harness.web_scraper.certification.probe",
            side_effect=TimeoutError("Synthetic preflight timeout"),
        ) as collect,
    ):
        with pytest.raises(TimeoutError, match="preflight"):
            certified_run(home, profile, "cloudflare-json", target)
        collect.assert_called_once()
    receipt = (target / "certification-precall.json").read_bytes()

    def score(home, run):
        value = {"scores": {}}
        save_json(run / "scores.json", value)
        return value

    with (
        collection_context(),
        patch("harness.web_scraper.certification.validate_dataset"),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=accepted) as call,
        patch("harness.web_scraper.certification.score_run", side_effect=score),
    ):
        certified_run(home, profile, "cloudflare-json", target)
        assert call.call_count == 3
    assert (target / "certification-precall.json").read_bytes() == receipt
    assert read_json(target / "certificate.json")["case_count"] == 3


def test_frozen_resume_uses_its_adapter_snapshot_not_current_adapter(tmp_path):
    source = tmp_path / "execution/pipeline.py"
    source.parent.mkdir()
    source.write_text(
        "from pathlib import Path\nPROFILE = Path('/obsolete/snapshot/profile.json')\n"
        "def validate_dataset(home, profile=PROFILE):\n return profile\n"
        "def probe(*args, **kwargs):\n"
        " from harness.web_scraper.stagehand import extract\n"
        ' return extract("frozen", {}), validate_dataset(None)\n'
    )
    adapter = source.with_name("stagehand.py")
    adapter.write_text('def extract(*args, **kwargs):\n return "frozen adapter"\n')
    save_json(tmp_path / "contract.json", {"stagehand_sha256": digest(adapter.read_bytes())})
    with patch("harness.web_scraper.stagehand.extract", return_value="current adapter"):
        collector = frozen_probe(tmp_path, digest(source.read_bytes()))
        from harness.web_scraper.certification import PROFILE

        assert collector() == ("frozen adapter", PROFILE)
        from harness.web_scraper.stagehand import extract as current

        assert current("url", {}) == "current adapter"


def test_raw_html_text_denial_preserves_status_and_body():
    from harness.web_scraper.pipeline import provider_call

    class Response:
        status_code = 402
        headers = {}
        raw = SimpleNamespace(read=lambda *args, **kwargs: b"<h1>Payment required</h1>")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    session = SimpleNamespace(post=lambda *args, **kwargs: Response())
    with (
        patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}),
        patch.dict("os.environ", {"DIFFBOT_API_TOKEN": "synthetic"}),
        patch.dict("harness.web_scraper.pipeline._RAW_SESSIONS", {}, clear=True),
    ):
        row = provider_call("diffbot-product", "<html>frozen</html>", "https://example.org")
    assert row["http_status"] == 402
    assert row["response"]["non_json_response"] == "<h1>Payment required</h1>"
    assert access_blocker(row) == "quota_or_payment"


def test_endpoint_permission_denial_pauses_without_quota_wording():
    assert (
        access_blocker({"http_status": 403, "response": {"message": "Forbidden"}}) == "permission"
    )
    assert (
        access_blocker({"http_status": 200, "response": {"error": "Could not download page (403)"}})
        is None
    )
    assert access_blocker({"http_status": 403, "response": {}}) == "permission"
    assert (
        access_blocker(
            {"http_status": 200, "response": {"success": False, "error": "Insufficient credits"}}
        )
        == "quota_or_permission"
    )


def test_repository_project_identifier_overrides_shell(tmp_path):
    import os

    from harness.web_fetch.config import load_runtime_env

    env = tmp_path / ".env"
    env.write_text("BROWSERBASE_PROJECT_ID=repository-project\n")
    with (
        patch("harness.web_fetch.config.ROOT", tmp_path),
        patch.dict(os.environ, {"BROWSERBASE_PROJECT_ID": "shell-project"}),
    ):
        load_runtime_env()
        assert os.environ["BROWSERBASE_PROJECT_ID"] == "repository-project"


def test_predispatch_storage_failure_does_not_complete_a_provider_attempt(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/storage-failure"

    def save(path, value, **kwargs):
        if path.name == "inflight.json":
            raise OSError("Synthetic disk full before dispatch")
        return save_json(path, value, **kwargs)

    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.save_json", side_effect=save),
        patch("harness.web_scraper.pipeline.provider_call") as call,
    ):
        with pytest.raises(OSError, match="disk full"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        call.assert_not_called()
    assert not (target / "calls.journal.jsonl").exists()
    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=accepted) as call,
    ):
        probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert call.call_count == 3


@pytest.mark.parametrize("suffix", [".html", ""])
def test_canonical_snapshot_paths_are_used_for_qualification_and_extraction(tmp_path, suffix):
    home = create(tmp_path / "example")
    target = home / "runs/canonical"
    urls = []

    def request(url, **kwargs):
        urls.append(url)
        cid = url.rsplit("/", 1)[-1].removesuffix(".html")
        raw = (home / "dataset/html" / (cid + ".html")).read_bytes()
        return HttpResponse(200, url, {}, raw)

    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.request", side_effect=request),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=accepted) as call,
    ):
        probe(
            home,
            ["cloudflare-json"],
            full=True,
            run_dir=target,
            fixture_base="https://frozen.example",
            fixture_suffix=suffix,
        )
    assert len(urls) == 6
    assert (
        all(url.endswith(suffix) for url in urls)
        if suffix
        else all(not url.endswith(".html") for url in urls)
    )
    assert [c.args[3] for c in call.call_args_list] == sorted(set(urls))


def test_redirecting_snapshot_is_rejected_before_any_provider_call(tmp_path):
    home = create(tmp_path / "example")

    def redirect(url, **kwargs):
        cid = url.rsplit("/", 1)[-1].removesuffix(".html")
        raw = (home / "dataset/html" / (cid + ".html")).read_bytes()
        return HttpResponse(200, url.removesuffix(".html"), {}, raw)

    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.request", side_effect=redirect),
        patch("harness.web_scraper.pipeline.provider_call") as call,
    ):
        with pytest.raises(ValueError, match="canonical frozen fixture"):
            probe(home, ["cloudflare-json"], full=True, fixture_base="https://frozen.example")
        call.assert_not_called()


def test_resumed_collection_observes_frozen_pacing_before_dispatch(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/pacing"
    clock = [0.0]
    started = []

    def sleep(seconds):
        clock[0] += seconds

    def save(path, value, **kwargs):
        if path.name == "inflight.json" and started:
            raise KeyboardInterrupt
        return save_json(path, value, **kwargs)

    def call(*args, **kwargs):
        started.append(clock[0])
        return accepted()

    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.time.monotonic", side_effect=lambda: clock[0]),
        patch("harness.web_scraper.pipeline.time.sleep", side_effect=sleep),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=call),
    ):
        with (
            patch("harness.web_scraper.pipeline.save_json", side_effect=save),
            pytest.raises(KeyboardInterrupt),
        ):
            probe(
                home,
                ["cloudflare-json"],
                full=True,
                run_dir=target,
                min_call_interval=13,
                post_call_cooldown=10,
            )
        resumed_at = clock[0]
        probe(
            home,
            ["cloudflare-json"],
            full=True,
            run_dir=target,
            min_call_interval=13,
            post_call_cooldown=10,
        )
    assert len(started) == 3
    assert started[1] - resumed_at >= 13


def test_stagehand_phases_share_one_timeout_budget():
    clock = [0.0]
    timeouts = []

    def request(url, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock[0] += min(50, kwargs["timeout"])
        body = {"success": True, "data": {"sessionId": "synthetic"}, "result": {"sku": "X"}}
        return HttpResponse(200, url, {}, json.dumps(body).encode())

    with (
        patch("harness.web_scraper.stagehand.time.monotonic", side_effect=lambda: clock[0]),
        patch("harness.web_scraper.stagehand.request", side_effect=request),
        patch.dict(
            "os.environ",
            {"BROWSERBASE_API_KEY": "synthetic", "BROWSERBASE_PROJECT_ID": "synthetic"},
        ),
    ):
        extract("https://frozen.example/1.html", {}, timeout=120)
    assert timeouts == [115, 65, 15, 5]
    assert clock[0] == 120


@pytest.mark.parametrize("snapshot_state", ["empty", "matching", "changed"])
def test_initialization_resumes_after_snapshot_before_contract(tmp_path, snapshot_state):
    import harness.web_scraper.pipeline as pipeline

    home = create(tmp_path / "example")
    target = home / "runs/interrupted-init"
    (target / "execution").mkdir(parents=True)
    if snapshot_state != "empty":
        (target / "execution/pipeline.py").write_bytes(
            Path(pipeline.__file__).read_bytes() if snapshot_state == "matching" else b"changed"
        )
    with (
        collection_context(),
        patch("harness.web_scraper.pipeline.provider_call", side_effect=accepted) as call,
    ):
        if snapshot_state == "changed":
            with pytest.raises(ValueError, match="snapshot changed"):
                probe(home, ["cloudflare-json"], full=True, run_dir=target)
            call.assert_not_called()
        else:
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
            assert call.call_count == 3


@pytest.mark.parametrize("transport", ["requests", "urllib"])
def test_continuously_streaming_response_has_a_total_deadline(transport):
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    import requests
    from urllib3.exceptions import ReadTimeoutError

    from harness.web_fetch.providers.http_client import request
    from harness.web_scraper.pipeline import total_deadline

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()

    @total_deadline
    def consume(*, timeout):
        url = f"http://127.0.0.1:{server.server_port}"
        if transport == "requests":
            with requests.get(url, stream=True, timeout=timeout) as response:
                response.raw.read()
        else:
            request(url, timeout=timeout)

    started = time.monotonic()
    try:
        with pytest.raises((TimeoutError, requests.RequestException, ReadTimeoutError)):
            consume(timeout=0.15)
        assert time.monotonic() - started < 0.8
    finally:
        server.shutdown()
        server.server_close()


def test_stagehand_timeout_retains_setup_failure_phase_and_cleanup():
    def request(url, **kwargs):
        if url.endswith("/navigate"):
            raise TimeoutError("Synthetic navigation timeout")
        body = {"success": True, "data": {"sessionId": "synthetic"}}
        return HttpResponse(200, url, {}, json.dumps(body).encode())

    with (
        patch("harness.web_scraper.stagehand.request", side_effect=request),
        patch.dict(
            "os.environ",
            {"BROWSERBASE_API_KEY": "synthetic", "BROWSERBASE_PROJECT_ID": "synthetic"},
        ),
    ):
        result = extract("https://frozen.example/1.html", {})
    assert result["state"] == "call_error"
    assert result["failure_phase"] == "snapshot_navigation"
    calls = result["response"]["calls"]
    assert [r["phase"] for r in calls] == [
        "session_setup",
        "snapshot_navigation",
        "session_cleanup",
    ]
    assert calls[0]["http_status"] == 200
    assert calls[1]["error"] == "Synthetic navigation timeout"
    assert calls[2]["http_status"] == 200


def test_streaming_stagehand_navigation_stops_in_time_to_release_session():
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            paths.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path.endswith("/navigate"):
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.01)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                body = json.dumps({"success": True, "data": {"sessionId": "test"}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with (
            patch("harness.web_scraper.stagehand.BASE", f"http://127.0.0.1:{server.server_port}"),
            patch.dict(
                "os.environ", {"BROWSERBASE_API_KEY": "test", "BROWSERBASE_PROJECT_ID": "test"}
            ),
        ):
            started = time.monotonic()
            result = extract("https://frozen.example/1", {}, timeout=0.5)
            assert time.monotonic() - started < 0.8
        assert result["failure_phase"] == "snapshot_navigation"
        assert paths == ["/sessions/start", "/sessions/test/navigate", "/sessions/test/end"]
        assert result["response"]["calls"][-1]["http_status"] == 200
    finally:
        server.shutdown()
        server.server_close()
