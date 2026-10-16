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
def test_fixed_native_rejects_per_case_hints_before_call(kind):
    provider = build_provider(kind, {"type": kind, "uniform_native_parameters": True})
    case = EvalCase("one", "one", "https://example.com/", "control", [],
        retrieval_hints={"completion": {"selector": ".answer"}})
    with pytest.raises(ValueError, match="per-case"):
        provider.fetch(case, 45)


@pytest.mark.parametrize("track,task_kind", [
    ("web", "body"), ("dynamic", "body"), ("challenge", "structure"),
    ("risk_control", "body"), ("document", "body"), ("document", "file"),
    ("image", "body"), ("image", "resource"), ("video", "body"),
    ("video", "resource"),
])
def test_fixed_native_accepts_public_task_routes_without_per_case_assistance(track, task_kind):
    provider = build_provider("fixture", {"type": "fixture", "uniform_native_parameters": True})
    hints = {"version": 3, "mode": "native"} if track == "dynamic" else {}
    case = EvalCase("one", "one", "https://example.com/", "control", [],
        track=track, task_kind=task_kind, retrieval_hints=hints)
    provider.check_native_call(case)


def test_fixed_native_rejects_case_specific_page_capabilities():
    provider = build_provider("fixture", {"type": "fixture", "uniform_native_parameters": True})
    case = EvalCase("dynamic-iframe", "frame", "https://example.com/", "control", [],
        track="dynamic", task_kind="body", retrieval_hints={
            "version": 3, "mode": "native", "contract": "iframe-fourteen-family-headings"})
    with pytest.raises(ValueError, match="per-case"):
        provider.check_native_call(case)


@pytest.mark.parametrize("track,task_kind", [("document", "resource"), ("image", "file"),
    ("unknown", "body")])
def test_fixed_native_rejects_undeclared_task_routes(track, task_kind):
    provider = build_provider("fixture", {"type": "fixture", "uniform_native_parameters": True})
    case = EvalCase("one", "one", "https://example.com/", "control", [],
        track=track, task_kind=task_kind)
    with pytest.raises(ValueError, match="declared task route"):
        provider.check_native_call(case)


@pytest.mark.parametrize("runs,retries", [(2, 0), (2, 1), (2, 3)])
def test_runner_rejects_extra_attempts_before_preflight(tmp_path, runs, retries):
    from harness.web_fetch.runner import run_evaluation
    provider = build_provider("fixture", {"type": "fixture", "uniform_native_parameters": True})
    with pytest.raises(ValueError, match="one run"):
        run_evaluation(output_dir=tmp_path / "run", dataset_meta={}, dataset_path=tmp_path / "absent.json",
            cases=[EvalCase("one", "one", "https://example.com/", "control", [])], providers=[provider], provider_config_path=tmp_path / "absent-providers.json",
            runs=runs, timeout=45, delay=0, max_failure_retries=retries)
    assert not (tmp_path / "run").exists()


def test_native_dataset_marker_reaches_real_runner_without_per_case_assistance(tmp_path):
    from harness.web_fetch.retrieval_hints import native_fetch_dataset
    from harness.web_fetch.runner import run_evaluation
    from harness.web_fetch.config import load_dataset
    source = {"id": "native-dynamic", "tool": "web_fetch", "cases": [{"id": "dynamic", "name": "Dynamic",
        "url": "https://example.com/", "category": "dynamic", "track": "dynamic", "expected_any": ["fixture"]}]}
    dataset = native_fetch_dataset(source)
    assert native_fetch_dataset(dataset) == dataset
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset))
    _, cases = load_dataset(path)
    provider = build_provider("native", {"type": "fixture", "uniform_native_parameters": True})
    config = tmp_path / "providers.json"
    config.write_text(json.dumps({"providers": {"native": {"type": "fixture", "uniform_native_parameters": True}}}))
    rows, _ = run_evaluation(output_dir=tmp_path / "run", dataset_meta=dataset, dataset_path=path,
        cases=cases, providers=[provider], provider_config_path=config, runs=1, timeout=45, delay=0)
    assert len(rows) == 1 and rows[0]["ok"]
    for extra in ({"selector": ".answer"}, {"delay_ms": 10}, {"completion": {}}, {"mode": "guided"}):
        changed = replace(cases[0], retrieval_hints={**cases[0].retrieval_hints, **extra})
        with pytest.raises(ValueError):
            provider.check_native_call(changed)
