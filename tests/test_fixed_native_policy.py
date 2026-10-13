import json
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import pytest
from harness.web_fetch.config import load_json
from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.base import build_provider
from harness.web_fetch.providers.http_client import HttpResponse


@pytest.mark.parametrize("kind,module,payload", [
    ("tavily_extract", "tavily", {"results": [{"url": "https://example.com/", "raw_content": "complete answer"}]}),
    ("exa_contents", "exa", {"results": [{"url": "https://example.com/", "text": "complete answer"}], "statuses": [{"status": "success"}]}),
    ("firecrawl", "firecrawl", {"success": True, "data": {"markdown": "complete answer", "metadata": {"statusCode": 200}}}),
    ("cloudflare_markdown", "cloudflare", {"success": True, "result": "complete answer"}),
    ("cloudflare_content", "cloudflare", {"success": True, "result": "complete answer"}),
])
def test_fixed_native_requests_preserve_capabilities_without_product_timers(kind, module, payload):
    provider = build_provider(module, {"type": kind, "uniform_native_parameters": True,
        "api_key": "fixture", "api_token": "fixture", "account_id": "fixture"})
    case = EvalCase("one", "one", "https://example.com/", "control", [])
    with patch(f"harness.web_fetch.providers.{module}.request") as call:
        call.return_value = HttpResponse(200, case.url, {}, json.dumps(payload).encode())
        result = provider.fetch(case, 45)
        assert call.call_count == 1
        assert call.call_args.kwargs["timeout"] == 45
        body = call.call_args.kwargs["json_body"]
        assert not ({"timeout", "livecrawlTimeout", "actionTimeout"} & body.keys())
        assert "timeout" not in body.get("gotoOptions", {})
        if module == "tavily": assert body["extract_depth"] == "advanced"
        if module == "cloudflare": assert body["gotoOptions"]["waitUntil"] == "networkidle2"
        if module == "firecrawl": assert body["maxAge"] == 0
        if module == "exa": assert body["maxAgeHours"] == 0
        assert result.content == "complete answer"
        call.reset_mock()
        call.return_value = HttpResponse(503, case.url, {}, b'{}')
        assert not provider.fetch(case, 45).ok
        assert call.call_count == 1


@pytest.mark.parametrize("kind", ["moli_webfetch", "firecrawl", "tavily_extract", "exa_contents",
    "cloudflare_markdown", "cowagent_builtin", "subprocess_jsonl"])
def test_fixed_native_rejects_hints_and_unknown_outputs_before_call(kind):
    provider = build_provider(kind, {"type": kind, "uniform_native_parameters": True})
    case = EvalCase("one", "one", "https://example.com/", "control", [],
        retrieval_hints={"completion": {"selector": ".answer"}})
    with pytest.raises(ValueError, match="per-case"):
        provider.fetch(case, 45)
    case = replace(case, retrieval_hints={}, task_kind="undeclared")
    with pytest.raises(ValueError, match="declared native output"):
        provider.fetch(case, 45)


@pytest.mark.parametrize("runs,retries", [(2, 0), (1, 1)])
def test_runner_rejects_extra_attempts_before_preflight(tmp_path, runs, retries):
    from harness.web_fetch.runner import run_evaluation
    provider = build_provider("fixture", {"type": "fixture", "uniform_native_parameters": True})
    with pytest.raises(ValueError, match="one run and zero caller retries"):
        run_evaluation(output_dir=tmp_path / "run", dataset_meta={}, dataset_path=tmp_path / "absent.json",
            cases=[EvalCase("one", "one", "https://example.com/", "control", [])], providers=[provider], provider_config_path=tmp_path / "absent-providers.json",
            runs=runs, timeout=45, delay=0, max_failure_retries=retries)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("kind,module", [("firecrawl", "firecrawl"), ("exa_contents", "exa"),
    ("tavily_extract", "tavily"), ("cloudflare_markdown", "cloudflare")])
def test_web_family_does_not_change_native_cloud_request(kind, module):
    provider = build_provider(module, {"type": kind, "uniform_native_parameters": True,
        "api_key": "fixture", "api_token": "fixture", "account_id": "fixture"})
    bodies = []
    for track in ("web", "dynamic", "challenge", "risk_control"):
        case = EvalCase("one", "one", "https://example.com/", "control", [], track=track,
            acceptance_contract=load_json(Path(__file__).resolve().parents[1] /
                "evaluation/dynamic-content-100-moli-webfetch/dataset.json")["cases"][0]["acceptance_contract"])
        with patch(f"harness.web_fetch.providers.{module}.request") as call:
            call.return_value = HttpResponse(200, case.url, {}, b'{}')
            provider.fetch(case, 45)
            assert call.call_count == 1
            bodies.append(call.call_args)
    assert all(body == bodies[0] for body in bodies)


def test_native_cloudflare_document_never_downloads_in_caller():
    provider = build_provider("cf", {"type": "cloudflare_markdown", "uniform_native_parameters": True})
    case = EvalCase("pdf", "pdf", "https://example.com/a.pdf", "control", [], track="document")
    with patch("harness.web_fetch.providers.cloudflare._get_document", side_effect=AssertionError("extra download")), \
         patch("harness.web_fetch.providers.cloudflare.request") as call:
        call.return_value = HttpResponse(200, case.url, {}, b'{"success":true,"result":"native text"}')
        assert provider.fetch(case, 45).content == "native text"
        assert call.call_count == 1
        assert call.call_args.args[0].endswith("/markdown")
        assert call.call_args.kwargs["json_body"]["url"] == case.url


def test_native_cloudflare_media_has_no_answer_dependent_selectors_or_timers():
    provider = build_provider("cf", {"type": "cloudflare_markdown", "uniform_native_parameters": True})
    bodies = []
    for expected in ([], [{"kind": "video-link", "urls": ["https://secret-answer.invalid/"]}]):
        case = EvalCase("media", "media", "https://example.com/", "control", [],
            track="video", task_kind="resource", expected_resources=expected)
        with patch("harness.web_fetch.providers.cloudflare.request") as call:
            call.return_value = HttpResponse(200, case.url, {}, b'{"success":true,"result":[]}')
            provider.fetch(case, 45)
            assert call.call_count == 1
            bodies.append(call.call_args.kwargs["json_body"])
    assert bodies[0] == bodies[1]
    assert "actionTimeout" not in bodies[0]
    assert "timeout" not in bodies[0]["gotoOptions"]


def test_all_430_frozen_cases_pass_native_input_guard():
    from pathlib import Path
    from harness.web_fetch.config import load_dataset
    provider = build_provider("fixture", {"type": "fixture", "uniform_native_parameters": True})
    count = 0
    for filename in ("dataset.json", "dynamic-content-100-moli-webfetch/dataset.json",
                     "popular-sites/dataset.json", "risk-control/dataset.json"):
        _, cases = load_dataset(Path(__file__).resolve().parents[1] / "evaluation" / filename)
        for case in cases:
            if filename == "dataset.json" and case.track not in {"web", "document", "image", "video"}:
                continue
            provider.check_native_call(case)
            count += 1
    assert count == 430


def test_native_media_projection_keeps_returned_sources_and_video_links():
    from harness.web_fetch.providers.cloudflare import _resource_urls
    payload = {"result": [
        {"selector": "video", "results": [{"attributes": [{"name": "src", "value": "/a.mp4"}]}]},
        {"selector": "a[href]", "results": [{"attributes": [{"name": "href", "value": "/video/abc"}]}]}]}
    assert _resource_urls(payload, base_url="https://example.com/", track="video",
        include_video_page_links=True, only_video_page_links=False) == [
            "https://example.com/a.mp4", "https://example.com/video/abc"]
