"""Interrupted, denied and resumed native API collection scenarios."""

import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from harness.web_extract.certification import run as certified_run
from harness.web_extract.example import create
from harness.web_extract.pipeline import access_blocker, load_run, probe, save_json
from harness.web_fetch.providers.http_client import HttpResponse


def collection_context():
    stack = ExitStack()
    stack.enter_context(patch("harness.web_extract.pipeline.validate_dataset"))
    stack.enter_context(patch("harness.web_extract.snapshots.validate_dataset"))
    stack.enter_context(patch("harness.web_extract.pipeline.load_runtime_env"))
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


@pytest.mark.parametrize("budget", [0, 2, True, "3", 24])
def test_product_call_budget_is_validated_before_dispatch(tmp_path, budget):
    home = create(tmp_path / "example")
    with collection_context(), patch("harness.web_extract.pipeline.provider_call") as call:
        with pytest.raises(ValueError, match="Physical call budget"):
            probe(
                home,
                ["cloudflare-json"],
                full=True,
                run_dir=home / "runs/budget",
                maximum_physical_calls=budget,
            )
        call.assert_not_called()


def test_product_call_budget_includes_access_retries_and_is_frozen_on_resume(tmp_path):
    from harness.web_extract.pipeline import read_json

    home = create(tmp_path / "example")
    run = home / "runs/budget"
    with (
        collection_context(),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 402, "response": {"error": "Insufficient credits"}},
        ) as call,
    ):
        with pytest.raises(PermissionError):
            probe(home, ["cloudflare-json"], full=True, run_dir=run, maximum_physical_calls=3)
        assert call.call_count == 1
    assert read_json(run / "contract.json")["maximum_physical_calls"] == 3
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
    ):
        with pytest.raises(ValueError, match="Resume physical call budget changed"):
            probe(home, ["cloudflare-json"], full=True, run_dir=run, maximum_physical_calls=4)
        call.assert_not_called()
        with pytest.raises(PermissionError, match="physical call budget exhausted"):
            probe(home, ["cloudflare-json"], full=True, run_dir=run, maximum_physical_calls=3)
        assert call.call_count == 2
    assert len((run / "calls.journal.jsonl").read_text(encoding="utf-8").splitlines()) == 3


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("header", ["Authorization", "X-BB-API-Key", "SGAI-APIKEY"])
def test_native_redirect_retains_response_without_sending_credentials_to_target(
    tmp_path, monkeypatch, status, header
):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from harness.web_extract import pipeline

    stolen = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            stolen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.headers[header] == "private-credential"
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(status)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/collect")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"redirect":true}')

        def log_message(self, *args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    workers = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (target, origin)
    ]
    for worker in workers:
        worker.start()
    try:
        url = f"http://127.0.0.1:{origin.server_port}/extract"
        monkeypatch.setitem(
            pipeline.METHODS,
            "cloudflare-json",
            {
                **pipeline.METHODS["cloudflare-json"],
                "adapter": lambda *args, **kwargs: (
                    url,
                    {header: "private-credential"},
                    {"html": "page"},
                ),
            },
        )
        result = pipeline.provider_call(
            "cloudflare-json", "page", "https://source.example", None, home=tmp_path
        )
        assert result["http_status"] == status
        assert result["request"]["endpoint"] == url
        assert result["response"] == {"redirect": True}
        assert not stolen
    finally:
        for server, worker in zip((target, origin), workers, strict=True):
            server.shutdown()
            worker.join()
            server.server_close()


def test_interrupted_inflight_request_is_not_silently_replayed(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/interrupted"
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", side_effect=KeyboardInterrupt) as call,
    ):
        with pytest.raises(KeyboardInterrupt):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert call.call_count == 1
    assert (target / "inflight.json").exists()
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
    ):
        with pytest.raises(ValueError, match="Ambiguous in-flight"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        call.assert_not_called()


@pytest.mark.parametrize("entry", ["plan", "run"])
def test_repository_destination_is_rejected_before_any_receipt_write(tmp_path, entry):
    from harness.web_extract.certification import create_plan

    root = tmp_path / "repository"
    root.mkdir()
    target = root / "private-evidence"
    with patch("harness.web_extract.pipeline.ROOT", root):
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
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
        patch("harness.web_extract.pipeline.save_json", side_effect=save),
    ):
        with pytest.raises(KeyboardInterrupt):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert call.call_count == 3
    assert (target / "remote-fixture-after.json").exists()
    with collection_context(), patch("harness.web_extract.pipeline.provider_call") as call:
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
    with patch("harness.web_extract.certification.probe") as call:
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
    from harness.web_extract.certification import create_plan
    from harness.web_extract.pipeline import read_json

    home = create(tmp_path / "example")
    profile, target = tmp_path / "plan.json", home / "runs/preflight"
    with (
        patch("harness.web_extract.certification.validate_dataset"),
        patch("harness.web_extract.snapshots.validate_dataset"),
    ):
        from tests.web_extract.snapshot_inputs import bind_inputs, serve_input

        snapshot = bind_inputs(home, tmp_path / "test-snapshots.json")
        create_plan(home, ["cloudflare-json"], profile, snapshots=snapshot)
    with (
        collection_context(),
        patch(
            "harness.web_extract.pipeline.qualify_fixture",
            side_effect=serve_input,
        ),
        patch("harness.web_extract.certification.validate_dataset"),
        patch(
            "harness.web_extract.certification.probe",
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
        patch(
            "harness.web_extract.pipeline.qualify_fixture",
            side_effect=serve_input,
        ),
        patch("harness.web_extract.certification.validate_dataset"),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
        patch("harness.web_extract.certification.score_run", side_effect=score),
    ):
        certified_run(home, profile, "cloudflare-json", target)
        assert call.call_count == 3
    assert (target / "certification-precall.json").read_bytes() == receipt
    assert read_json(target / "certificate.json")["case_count"] == 3


def test_raw_html_text_denial_preserves_status_and_body():
    from harness.web_extract.pipeline import provider_call

    class Response:
        status_code = 402
        headers = {}
        raw = SimpleNamespace(read=lambda *args, **kwargs: b"<h1>Payment required</h1>")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    session = SimpleNamespace(
        post=lambda *args, **kwargs: Response(), cookies=SimpleNamespace(clear=lambda: None)
    )
    with (
        patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}),
        patch.dict("os.environ", {"DIFFBOT_API_TOKEN": "synthetic"}),
        patch.dict("harness.web_extract.pipeline._RAW_SESSIONS", {}, clear=True),
    ):
        row = provider_call("diffbot-product", "<html>frozen</html>", "https://example.org")
    assert row["http_status"] == 402
    assert row["response"]["non_json_response"] == "<h1>Payment required</h1>"
    assert access_blocker(row) == "quota_or_payment"


def test_endpoint_permission_denial_pauses_without_quota_wording():
    assert access_blocker({"http_status": 403, "response": {"message": "Forbidden"}}) is None
    assert (
        access_blocker({"http_status": 200, "response": {"error": "Could not download page (403)"}})
        is None
    )
    assert access_blocker({"http_status": 403, "response": {}}) is None
    assert (
        access_blocker(
            {"http_status": 200, "response": {"success": False, "error": "Insufficient credits"}}
        )
        == "quota_or_permission"
    )


def test_predispatch_storage_failure_does_not_complete_a_provider_attempt(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/storage-failure"

    def save(path, value, **kwargs):
        if path.name == "inflight.json":
            raise OSError("Synthetic disk full before dispatch")
        return save_json(path, value, **kwargs)

    with (
        collection_context(),
        patch("harness.web_extract.pipeline.save_json", side_effect=save),
        patch("harness.web_extract.pipeline.provider_call") as call,
    ):
        with pytest.raises(OSError, match="disk full"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        call.assert_not_called()
    assert not (target / "calls.journal.jsonl").exists()
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
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
        patch("harness.web_extract.pipeline.request", side_effect=request),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
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
        patch("harness.web_extract.pipeline.request", side_effect=redirect),
        patch("harness.web_extract.pipeline.provider_call") as call,
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
        patch("harness.web_extract.pipeline.time.monotonic", side_effect=lambda: clock[0]),
        patch("harness.web_extract.pipeline.time.sleep", side_effect=sleep),
        patch("harness.web_extract.pipeline.provider_call", side_effect=call),
    ):
        with (
            patch("harness.web_extract.pipeline.save_json", side_effect=save),
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


@pytest.mark.parametrize("snapshot_state", ["empty", "matching", "changed"])
def test_initialization_resumes_after_snapshot_before_contract(tmp_path, snapshot_state):
    import harness.web_extract.pipeline as pipeline

    home = create(tmp_path / "example")
    target = home / "runs/interrupted-init"
    (target / "execution").mkdir(parents=True)
    if snapshot_state != "empty":
        (target / "execution/pipeline.py").write_bytes(
            Path(pipeline.__file__).read_bytes() if snapshot_state == "matching" else b"changed"
        )
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
    ):
        probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert call.call_count == 3
        assert "pipeline_snapshot" not in pipeline.read_json(target / "contract.json")


@pytest.mark.parametrize("transport", ["requests", "urllib", "fixture"])
def test_continuously_streaming_response_has_a_total_deadline(transport):
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event, Thread

    import requests
    from urllib3.exceptions import ReadTimeoutError

    from harness.web_extract.pipeline import total_deadline
    from harness.web_fetch.providers.http_client import request

    connected = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            connected.set()
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
        elif transport == "fixture":
            total_deadline(request)(url, timeout=timeout)
        else:
            request(url, timeout=timeout)

    started = time.monotonic()
    try:
        with pytest.raises((TimeoutError, requests.RequestException, ReadTimeoutError)):
            (consume.__wrapped__ if transport == "fixture" else consume)(
                timeout=0.5 if transport == "fixture" else 0.15
            )
        assert connected.is_set(), "Deadline must interrupt a connected streaming reader"
        assert time.monotonic() - started < (1.2 if transport == "fixture" else 0.8)
    finally:
        server.shutdown()
        server.server_close()


def test_json_checkpoint_survives_partial_write_and_exclusive_collision(tmp_path):
    from harness.web_extract import pipeline

    target = tmp_path / "checkpoint.json"
    pipeline.save_json(target, {"committed": True})
    original = target.read_bytes()

    def interrupted(value, stream, **kwargs):
        stream.write('{"partial":')
        raise KeyboardInterrupt

    with patch.object(pipeline.json, "dump", side_effect=interrupted):
        with pytest.raises(KeyboardInterrupt):
            pipeline.save_json(target, {"next": True})
        with pytest.raises(KeyboardInterrupt):
            pipeline.save_json(tmp_path / "new.json", {}, exclusive=True)
    assert target.read_bytes() == original
    assert not (tmp_path / "new.json").exists()
    with pytest.raises(FileExistsError):
        pipeline.save_json(target, {"overwrite": True}, exclusive=True)
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_repeated_missing_credentials_do_not_exhaust_full_run(tmp_path):
    home = create(tmp_path / "example")
    target = home / "runs/no-key"
    with (
        collection_context(),
        patch.dict("os.environ", {}, clear=True),
        patch("harness.web_extract.pipeline.provider_call") as call,
    ):
        for _ in range(3):
            with pytest.raises(PermissionError, match="Missing credentials"):
                probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert not (target / "calls.journal.jsonl").exists()
        call.assert_not_called()
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
    ):
        probe(home, ["cloudflare-json"], full=True, run_dir=target)
        assert call.call_count == 3


@pytest.mark.parametrize(
    "response",
    [
        {"success": False, "error": "session expired"},
        {
            "success": False,
            "error": "page could not load",
            "content": "account balance upgrade credits",
        },
    ],
)
def test_target_error_content_never_spends_account_retry_budget(response):
    assert access_blocker({"http_status": 200, "response": response}) is None


@pytest.mark.parametrize(
    "method,bad,good",
    [
        ("firecrawl-json", {"data": None}, {"data": {"json": {"sku": "ok"}}}),
        ("scrapfly-product", {"result": None}, {"data": {"identifiers": {"sku": "ok"}}}),
        (
            "scrapfly-product",
            {"result": {"extracted_data": None}},
            {"result": {"extracted_data": {"data": {"identifiers": {"sku": "ok"}}}}},
        ),
        ("diffbot-product", {"objects": None}, {"objects": [{"sku": "ok"}]}),
        (
            "diffbot-product",
            {"objects": [None]},
            {"objects": [{"offerPriceDetails": None, "sku": "ok"}]},
        ),
        ("oxylabs-json", {"results": [None]}, {"results": [{"json": {"sku": "ok"}}]}),
    ],
)
def test_null_native_envelopes_are_safe_at_every_projection_boundary(method, bad, good):
    from harness.web_extract.pipeline import normalize

    assert normalize(method, bad) is None
    assert normalize(method, good)["sku"] == "ok"


def test_encoded_credentials_are_removed_from_retained_exception_messages():
    from urllib.parse import urlencode

    from harness.web_extract.pipeline import redact

    secret = "synthetic+token/with=encoded?parts"
    error = "SSL failure fetching https://api.example.test/?" + urlencode({"token": secret})
    with patch.dict("os.environ", {"DIFFBOT_API_TOKEN": secret}):
        safe = redact({"error": error})["error"]
    assert secret not in safe
    assert urlencode({"token": secret}).split("=", 1)[1] not in safe
    assert "[REDACTED]" in safe


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_incomplete_direct_resume_rejects_damaged_snapshot_before_dispatch(tmp_path, damage):
    home = create(tmp_path / "example")
    target = home / "runs/snapshot-damage"
    with (
        collection_context(),
        patch(
            "harness.web_extract.pipeline.provider_call",
            side_effect=[accepted(), KeyboardInterrupt],
        ) as call,
    ):
        with pytest.raises(KeyboardInterrupt):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
    # The in-flight request is unresolved; the snapshot must fail first, without dispatch.
    # Model an older retained run that explicitly binds a source snapshot.
    import harness.web_extract.pipeline as pipeline

    contract = pipeline.read_json(target / "contract.json")
    contract["pipeline_snapshot"] = "execution/pipeline.py"
    pipeline.save_json(target / "contract.json", contract)
    snapshot = target / "execution/pipeline.py"
    snapshot.parent.mkdir(exist_ok=True)
    snapshot.write_bytes(Path(pipeline.__file__).read_bytes())
    snapshot.unlink() if damage == "missing" else snapshot.write_text(
        "# altered execution", encoding="utf-8"
    )
    with collection_context(), patch("harness.web_extract.pipeline.provider_call") as call:
        with pytest.raises(ValueError, match="Frozen execution source changed"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
    call.assert_not_called()


@pytest.mark.parametrize("text", ['{"price":NaN}', '{"nested":[Infinity]}', '{"price":1e400}'])
@pytest.mark.parametrize("route", ["diffbot-product", "scrapfly-product", "cloudflare-json"])
def test_nonfinite_native_reply_is_checkpoint_safe(tmp_path, text, route):
    from harness.web_extract.pipeline import provider_call, read_json

    class Response:
        status_code = 200
        headers = {}
        raw = SimpleNamespace(read=lambda *args, **kwargs: text.encode())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with (
        patch.dict(
            sys.modules,
            {
                "requests": SimpleNamespace(
                    Session=lambda: SimpleNamespace(
                        post=lambda *args, **kwargs: Response(),
                        cookies=SimpleNamespace(clear=lambda: None),
                    )
                )
            },
        ),
        patch.dict("harness.web_extract.pipeline._RAW_SESSIONS", {}, clear=True),
        patch.dict(
            "os.environ",
            {
                "DIFFBOT_API_TOKEN": "synthetic",
                "SCRAPFLY_API_KEY": "synthetic",
                "CLOUDFLARE_ACCOUNT_ID": "synthetic",
                "CLOUDFLARE_API_TOKEN": "synthetic",
            },
        ),
        patch(
            "harness.web_extract.pipeline.request",
            return_value=HttpResponse(200, "https://example.org", {}, text.encode()),
        ),
    ):
        row = provider_call(route, "<html>frozen</html>", "https://example.org")
    path = tmp_path / "completed-call.json"
    save_json(path, row)
    assert read_json(path)["response"] == {"non_json_response": text}
    assert row["http_status"] == 200 and row["elapsed_seconds"] >= 0


@pytest.mark.parametrize(
    "mutation",
    [
        "earlier-logical",
        "missing-physical",
        "journal-omission",
        "successful-retry",
        "wrong-attempt",
        "exceeded-budget",
        "missing-journal",
    ],
)
def test_aggregation_reconciles_retry_history_and_final_selection(tmp_path, mutation):
    import json
    from copy import deepcopy

    from harness.web_extract.pipeline import digest, read_json

    home = create(tmp_path / "example")
    run = home / "runs/example-v1"
    manifest = read_json(run / "manifest.json")
    contract = read_json(run / "contract.json")
    contract["access_retry_limit"] = 1
    save_json(run / "contract.json", contract)
    manifest["contract_sha256"] = digest((run / "contract.json").read_bytes())
    entry = manifest["calls"][-1]
    first = read_json(run / entry["path"])
    first.update(state="response_received", http_status=402, physical_attempt=1)
    save_json(run / entry["path"], first)
    entry["sha256"] = digest((run / entry["path"]).read_bytes())
    final = {
        **first,
        "physical_attempt": 2,
        "http_status": 200,
        "elapsed_seconds": 0.2,
        "retry_execution_seconds": first["elapsed_seconds"],
        "compatibility": "structured_output",
        "response": {"json": {"sku": "recovered"}},
    }
    path = run / "calls/final.json"
    save_json(path, final)
    last = {"path": "calls/final.json", "sha256": digest(path.read_bytes())}
    manifest["physical_calls"] = deepcopy(manifest["calls"]) + [last]
    manifest["calls"][-1] = last
    journal = run / "calls.journal.jsonl"
    journal.write_text(
        "".join(json.dumps(e) + "\n" for e in manifest["physical_calls"]), encoding="utf-8"
    )
    save_json(run / "manifest.json", manifest)
    assert load_run(run)[1][-1]["physical_attempt"] == 2
    if mutation == "missing-journal":
        journal.unlink()
    elif mutation == "exceeded-budget":
        contract["access_retry_limit"] = 0
        save_json(run / "contract.json", contract)
        manifest["contract_sha256"] = digest((run / "contract.json").read_bytes())
    elif mutation == "earlier-logical":
        manifest["calls"][-1] = entry
    elif mutation == "missing-physical":
        manifest["physical_calls"].remove(entry)
    elif mutation == "journal-omission":
        journal.write_text(
            "".join(json.dumps(e) + "\n" for e in manifest["physical_calls"][:-1]), encoding="utf-8"
        )
    elif mutation == "wrong-attempt":
        final["physical_attempt"] = 3
        save_json(path, final)
        last["sha256"] = digest(path.read_bytes())
    else:
        first.update(http_status=200, compatibility="structured_output")
        save_json(run / entry["path"], first)
        manifest["physical_calls"][-2]["sha256"] = digest((run / entry["path"]).read_bytes())
    save_json(run / "manifest.json", manifest)
    with pytest.raises(ValueError, match="final physical|attempt sequence|journal|retry|Retry"):
        load_run(run)


@pytest.mark.parametrize("wire", ["json", "text"])
def test_zyte_basic_encoding_is_removed_from_retained_response(tmp_path, wire):
    import base64
    import json

    from harness.web_extract.pipeline import provider_call, read_json

    secret = "zyte-synthetic-secret-for-regression"
    encoded = base64.b64encode((secret + ":").encode()).decode()
    text = "Upstream diagnostic: Basic " + encoded
    body = json.dumps({"message": text}).encode() if wire == "json" else text.encode()
    with (
        patch.dict("os.environ", {"ZYTE_API_KEY": secret}),
        patch(
            "harness.web_extract.pipeline.request",
            return_value=HttpResponse(502, "https://example.org", {}, body),
        ),
    ):
        row = provider_call("zyte-product", "<html>frozen</html>", "https://example.org")
        path = tmp_path / "retained.json"
        save_json(path, row)
    assert encoded not in path.read_text(encoding="utf-8") and secret not in path.read_text(
        encoding="utf-8"
    )
    assert "[REDACTED]" in json.dumps(read_json(path))


def test_zyte_basic_encoding_is_removed_from_checkpointed_transport_errors(tmp_path):
    import base64

    from harness.web_extract.pipeline import read_json

    home = create(tmp_path / "example")
    target = home / "runs/encoded-denial"
    secret = "zyte-synthetic-secret-for-regression"
    encoded = base64.b64encode((secret + ":").encode()).decode()
    with (
        patch("harness.web_extract.pipeline.validate_dataset"),
        patch("harness.web_extract.pipeline.load_runtime_env"),
        patch.dict("os.environ", {"ZYTE_API_KEY": secret}),
        patch("harness.web_extract.pipeline.request", side_effect=RuntimeError("Basic " + encoded)),
    ):
        probe(home, ["zyte-product"], full=True, run_dir=target)
    calls = list((target / "calls").glob("*.json"))
    assert len(calls) == 3
    for path in calls:
        assert encoded not in path.read_text(encoding="utf-8") and secret not in path.read_text(
            encoding="utf-8"
        )
        assert read_json(path)["error"] == "Basic [REDACTED]"


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "wrong-attempt",
        "boolean-attempt",
        "wrong-method",
        "wrong-case",
        "incomplete-journal",
    ],
)
def test_invalid_partial_history_stops_before_any_resumed_provider_call(tmp_path, mutation):
    import json

    from harness.web_extract.pipeline import digest, read_json

    home = create(tmp_path / "example")
    target = home / "runs/paused"
    denied = {
        "state": "response_received",
        "http_status": 402,
        "response": {"error": "Insufficient credits"},
    }
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", return_value=denied),
    ):
        with pytest.raises(PermissionError):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
    journal = target / "calls.journal.jsonl"
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1 and not (target / "inflight.json").exists()
    if mutation == "duplicate":
        entries.append(dict(entries[0]))
    elif mutation != "incomplete-journal":
        path = target / entries[0]["path"]
        row = read_json(path)
        row[
            {
                "wrong-attempt": "physical_attempt",
                "boolean-attempt": "physical_attempt",
                "wrong-method": "method",
                "wrong-case": "case_id",
            }[mutation]
        ] = (
            3
            if mutation == "wrong-attempt"
            else True
            if mutation == "boolean-attempt"
            else "outside-frozen-scope"
        )
        save_json(path, row)
        entries[0]["sha256"] = digest(path.read_bytes())
    raw = "".join(json.dumps(row) + "\n" for row in entries)
    journal.write_text(
        raw.rstrip("\n") if mutation == "incomplete-journal" else raw, encoding="utf-8"
    )
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as dispatch,
    ):
        with pytest.raises(ValueError, match="inventory|sequence|scope|journal"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        dispatch.assert_not_called()
    assert not (target / "manifest.json").exists()


@pytest.mark.parametrize(
    "entry", ["prepare", "reproduce", "report", "example", "baseline", "score", "materialize"]
)
def test_generated_outputs_are_rejected_inside_source_before_any_write(tmp_path, entry):
    from harness.web_extract import example, pipeline, report

    root = tmp_path / "repository"
    root.mkdir()
    output = root / "generated"
    with (
        patch.object(pipeline, "ROOT", root),
        patch.object(pipeline, "request") as network,
        pytest.raises(ValueError, match="outside the source repository"),
    ):
        if entry == "prepare":
            pipeline.prepare(output)
        elif entry == "reproduce":
            pipeline.reproduce(output)
        elif entry == "report":
            report.build(output, tmp_path / "absent.json", home=tmp_path)
        elif entry == "example":
            example.create(output)
        elif entry == "baseline":
            pipeline.baseline_extractor(output)
        elif entry == "materialize":
            from harness.web_extract.dataset import materialize

            materialize(output, {})
        else:
            pipeline.score_run(tmp_path, output)
    network.assert_not_called()
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("artifact", ["calls", "calls.journal.jsonl", "execution/pipeline.py"])
def test_resume_rejects_symlinked_storage_before_dispatch(tmp_path, artifact):
    home = create(tmp_path / "example")
    target = home / "runs/unsafe-storage"
    target.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    link = target / artifact
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external if artifact == "calls" else external / "uncreated")
    with collection_context(), patch("harness.web_extract.pipeline.provider_call") as dispatch:
        with pytest.raises(ValueError, match="Unsafe output"):
            probe(home, ["cloudflare-json"], full=True, run_dir=target)
        dispatch.assert_not_called()
    assert list(external.iterdir()) == []


@pytest.mark.parametrize("secret", ["abc123", "a/b?", "a"])
def test_short_active_credentials_are_redacted_without_corrupting_ids(monkeypatch, secret):
    import base64
    from urllib.parse import quote, quote_plus

    from harness.web_extract.pipeline import redact

    monkeypatch.setenv("ZYTE_API_KEY", secret)
    forms = {
        secret,
        quote(secret, safe=""),
        quote_plus(secret, safe=""),
        base64.b64encode((secret + ":").encode()).decode(),
    }
    for form in forms:
        assert redact({"error": "Authorization: Basic " + form})["error"].endswith("[REDACTED]")
        assert redact({"url": "https://example.org/?key=" + form})["url"].endswith("[REDACTED]")
    assert redact({"method": "zyte-product", "case_id": "1a2b3c", "state": "call_error"}) == {
        "method": "zyte-product",
        "case_id": "1a2b3c",
        "state": "call_error",
    }


@pytest.mark.parametrize(
    "message,blocked",
    [("Insufficient credits", True), ("Invalid API key", True), ("Rate limit", False)],
)
def test_plain_text_429_denials_pause_without_confusing_transient_limits(
    tmp_path, message, blocked
):
    home = create(tmp_path / "example")
    response = {"http_status": 429, "response": {"non_json_response": message}}
    with (
        collection_context(),
        patch("harness.web_extract.pipeline.provider_call", return_value=response) as call,
    ):
        if blocked:
            with pytest.raises(PermissionError):
                probe(home, ["cloudflare-json"], full=True)
            assert call.call_count == 1
        else:
            probe(home, ["cloudflare-json"], full=True)
            assert call.call_count == 3


def test_new_versioned_cohort_checks_dataset_manifest_without_page_fetches(tmp_path):
    home = create(tmp_path / "example")
    base = "https://snapshot.example/frozen"
    from harness.web_extract.snapshots import inventory

    with collection_context():
        version, cases = inventory(home)
    receipt = {
        "hosting_provider": "cloudflare",
        "mode": "dataset_manifest",
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "manifest_url": "https://snapshot.example/snapshots.json",
        "manifest_sha256": "a" * 64,
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "fixture_base": base,
    }
    with (
        collection_context(),
        patch("harness.web_extract.snapshots.check_manifest", return_value=receipt) as manifest,
        patch("harness.web_extract.pipeline.qualify_fixture") as page,
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as call,
    ):
        result = probe(
            home,
            ["cloudflare-json"],
            full=True,
            run_dir=home / "runs/versioned",
            fixture_base=base,
            fixture_suffix="",
            snapshot_manifest=receipt,
        )
    assert call.call_count == 3
    assert manifest.call_count == 2
    page.assert_not_called()
    assert len(load_run(result)[1]) == 3


def test_changed_dataset_snapshot_blocks_native_calls_and_repair(tmp_path):
    home = create(tmp_path / "example")
    base = "https://snapshot.example/frozen"
    receipt = {
        "manifest_url": "https://snapshot.example/snapshots.json",
        "manifest_sha256": "a" * 64,
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "fixture_base": base,
    }
    from harness.web_extract.repair import fixture_check

    with (
        collection_context(),
        patch(
            "harness.web_extract.snapshots.check_manifest",
            return_value={**receipt, "manifest_sha256": "changed"},
        ),
        patch("harness.web_extract.pipeline.provider_call") as call,
    ):
        with pytest.raises(ValueError, match="snapshot manifest changed"):
            probe(
                home,
                ["cloudflare-json"],
                full=True,
                run_dir=home / "runs/changed",
                fixture_base=base,
                fixture_suffix="",
                snapshot_manifest=receipt,
            )
        call.assert_not_called()
        with pytest.raises(ValueError, match="snapshot manifest changed"):
            fixture_check(home, base, ["any-case"], snapshot_manifest=receipt)


@pytest.mark.parametrize("status", [200, 403, 429])
@pytest.mark.parametrize(
    "message", ["Target page blocked", "Download failed: forbidden", "Target page mentions credits"]
)
def test_target_failures_do_not_pause_the_account(status, message):
    row = {"http_status": status, "response": {"success": False, "error": message}}
    assert access_blocker(row) is None


def test_extraction_error_containing_a_field_name_is_not_account_quota():
    assert (
        access_blocker(
            {"http_status": 200, "response": {"error": "Cannot extract credit card field"}}
        )
        is None
    )


@pytest.mark.parametrize("status", [200, 402, 429])
@pytest.mark.parametrize(
    "message",
    ["Insufficient credits to fetch target URL", "Invalid API key while fetching target page"],
)
def test_account_denial_is_not_hidden_by_target_fetch_words(status, message):
    result = access_blocker({"http_status": status, "response": {"error": message}})
    assert result in {"authentication", "quota_or_payment", "quota_or_permission"}


@pytest.mark.parametrize(
    "status, text, expected",
    [
        (401, "Failed to fetch target", "authentication"),
        (402, "Target page blocked", "quota_or_payment"),
        (429, "Your credit balance is too low", "quota_or_permission"),
        (403, "Target page forbidden", None),
        (403, "", None),
    ],
)
def test_account_status_and_target_denial_have_distinct_scope(status, text, expected):
    assert access_blocker({"http_status": status, "response": {"error": text}}) == expected


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("hosting_provider", ["external", None])
def test_external_manifest_does_not_replace_page_audit_during_collection(
    tmp_path, changed, hosting_provider
):
    from tests.web_extract.snapshot_inputs import serve_input

    home = create(tmp_path / "external-inputs")
    base = "https://external.example"
    calls = []

    def qualify(home, base, cid, **kwargs):
        calls.append(cid)
        if changed:
            raise ValueError("Remote fixture differs")
        return serve_input(home, base, cid)

    with (
        collection_context(),
        patch("harness.web_extract.snapshots.verify_bound_manifest", return_value=[]),
        patch("harness.web_extract.pipeline.qualify_fixture", side_effect=qualify),
        patch("harness.web_extract.pipeline.provider_call", side_effect=accepted) as provider,
    ):
        options = dict(
            full=True,
            run_dir=tmp_path / "external-run",
            fixture_base=base,
            native_options={"input_route": "url"},
            snapshot_manifest={"hosting_provider": hosting_provider},
        )
        if changed:
            with pytest.raises(ValueError, match="Remote fixture differs"):
                probe(home, ["cloudflare-json"], **options)
            provider.assert_not_called()
        else:
            probe(home, ["cloudflare-json"], **options)
            assert len(calls) == 2 * provider.call_count == 6


@pytest.mark.parametrize("method", ["diffbot-product", "scrapfly-product"])
def test_pooled_api_connections_never_inherit_response_cookies(method, monkeypatch):
    import io
    from email.message import Message

    import requests
    from requests.adapters import BaseAdapter

    from harness.web_extract.pipeline import provider_call

    sent = []

    class Adapter(BaseAdapter):
        def send(self, request, **kwargs):
            sent.append(request.headers.get("Cookie"))
            response = requests.Response()
            response.status_code = 200
            response.url = request.url
            response.request = request
            message = Message()
            message.add_header("Set-Cookie", "previous_page=one; Path=/")

            class Body(io.BytesIO):
                _original_response = SimpleNamespace(msg=message)

                def read(self, size=-1, decode_content=False):
                    return super().read(size)

            response.raw = Body(b"{}")
            return response

        def close(self):
            pass

    session = requests.Session()
    session.mount("https://", Adapter())
    monkeypatch.setenv("DIFFBOT_API_TOKEN", "synthetic")
    monkeypatch.setenv("SCRAPFLY_API_KEY", "synthetic")
    with patch.dict("harness.web_extract.pipeline._RAW_SESSIONS", {method: session}, clear=True):
        for _ in range(2):
            result = provider_call(method, "<p>Frozen</p>", "https://example.org")
            assert session.cookies.get("previous_page") == "one"
            assert result["request"]["cookie_policy"] == "no_inherited_api_cookies"
    assert sent == [None, None]
