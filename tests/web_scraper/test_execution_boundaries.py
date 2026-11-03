"""Interrupted, denied and resumed native API collection scenarios."""

import json
import sys
from contextlib import ExitStack
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


def test_frozen_resume_uses_its_adapter_snapshot_not_current_adapter(tmp_path):
    source = tmp_path / "execution/pipeline.py"
    source.parent.mkdir()
    source.write_text(
        "def probe(*args, **kwargs):\n"
        " from harness.web_scraper.stagehand import extract\n"
        ' return extract("frozen", {})\n'
    )
    adapter = source.with_name("stagehand.py")
    adapter.write_text('def extract(*args, **kwargs):\n return "frozen adapter"\n')
    save_json(tmp_path / "contract.json", {"stagehand_sha256": digest(adapter.read_bytes())})
    with patch("harness.web_scraper.stagehand.extract", return_value="current adapter"):
        collector = frozen_probe(tmp_path, digest(source.read_bytes()))
        assert collector() == "frozen adapter"
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
