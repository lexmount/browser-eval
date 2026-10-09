import json
from dataclasses import replace
from unittest.mock import patch
import pytest
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
def test_fixed_native_rejects_hints_and_auxiliary_routes_before_call(kind):
    provider = build_provider(kind, {"type": kind, "uniform_native_parameters": True})
    case = EvalCase("one", "one", "https://example.com/", "control", [],
        retrieval_hints={"completion": {"selector": ".answer"}})
    with pytest.raises(ValueError, match="per-case"):
        provider.fetch(case, 45)
    case = replace(case, retrieval_hints={}, track="document")
    with pytest.raises(ValueError, match="auxiliary"):
        provider.fetch(case, 45)


@pytest.mark.parametrize("runs,retries", [(2, 0), (1, 1)])
def test_runner_rejects_extra_attempts_before_preflight(tmp_path, runs, retries):
    from harness.web_fetch.runner import run_evaluation
    provider = build_provider("fixture", {"type": "fixture", "uniform_native_parameters": True})
    with pytest.raises(ValueError, match="one run and zero caller retries"):
        run_evaluation(output_dir=tmp_path / "run", dataset_meta={}, dataset_path=tmp_path / "absent.json",
            cases=[], providers=[provider], provider_config_path=tmp_path / "absent-providers.json",
            runs=runs, timeout=45, delay=0, max_failure_retries=retries)
    assert not (tmp_path / "run").exists()
