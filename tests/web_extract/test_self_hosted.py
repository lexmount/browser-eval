"""Hosted open-source extractors obey the same frozen-input and field contracts."""

from unittest.mock import patch

import pytest

from harness.web_extract.pipeline import SCHEMA, normalize, provider_call, validate_native_options
from harness.web_extract.self_hosted import validate_input

RUNTIME = {
    "engine": "crawl4ai",
    "version": "synthetic-1",
    "model": "test-model",
    "model_base": "https://model.example",
    "implementation_sha256": "a" * 64,
    "environment_sha256": "b" * 64,
}
OPTIONS = {"endpoint": "https://extract.example", "runtime_identity": RUNTIME}


@pytest.mark.parametrize(
    "url",
    [
        "https://live.example/page",
        "http://snapshots.example/page",
        "https://user:secret@snapshots.example/page",
        "https://snapshots.example.evil/page",
    ],
)
def test_only_configured_frozen_origins_are_accepted(monkeypatch, url):
    monkeypatch.setenv("SNAPSHOT_ORIGINS", "https://snapshots.example")
    with pytest.raises(ValueError, match="snapshot origin"):
        validate_input({"url": url, "schema": SCHEMA, "engine": "crawl4ai"})


def test_changed_runtime_cannot_silently_resume(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_ORIGINS", "https://snapshots.example")
    value = {
        "url": "https://snapshots.example/page",
        "schema": SCHEMA,
        "engine": "crawl4ai",
        "expected_runtime": RUNTIME,
    }
    with patch("harness.web_extract.self_hosted.runtime_identity", return_value=RUNTIME):
        assert validate_input(value) == value
    with patch(
        "harness.web_extract.self_hosted.runtime_identity",
        return_value={**RUNTIME, "version": "changed"},
    ):
        with pytest.raises(ValueError, match="frozen extraction configuration"):
            validate_input(value)


@pytest.mark.parametrize(
    "method",
    ["crawl4ai-json", "scrapegraph-oss-json", "stagehand-local-json", "firecrawl-oss-json"],
)
def test_open_source_projections_keep_missing_and_multiple_field_values(method):
    schema = {
        "type": "object",
        "properties": {"name": {"type": "array"}, "phone": {"type": "array"}},
    }
    data = {"name": ["First", "Second"], "phone": []}
    assert normalize(method, {"success": True, "data": data}, schema=schema) == data
    assert normalize(method, {"success": False, "data": data}, schema=schema) is None
    assert normalize(method, {"success": True, "data": []}, schema=schema) is None


@pytest.mark.parametrize("flag", [None, 0, 1, "true"])
def test_self_hosted_data_requires_an_explicit_successful_worker_result(flag):
    payload = {"success": flag, "data": {"sku": "unaccepted"}}
    assert normalize("crawl4ai-json", payload) is None
    payload.pop("success")
    assert normalize("crawl4ai-json", payload) is None


def test_native_request_uses_snapshot_and_shared_schema_without_ground_truth(monkeypatch):
    monkeypatch.setenv("EXTRACTION_API_KEY", "synthetic-secret")
    from types import SimpleNamespace

    response = SimpleNamespace(
        status=200, headers={}, text=lambda: '{"success":true,"data":{"price":"12.99"}}'
    )
    with patch("harness.web_extract.pipeline.request", return_value=response) as request:
        result = provider_call(
            "crawl4ai-json",
            "<h1>Product</h1>",
            "https://original.example/item",
            "https://snapshots.example/hash/case",
            native_options=OPTIONS,
            schema=SCHEMA,
            prompt="Copy the main record.",
        )
    args = request.call_args
    assert args.args == ("https://extract.example/extract/crawl4ai",)
    body = args.kwargs["json_body"]
    assert body["url"] == "https://snapshots.example/hash/case"
    assert body["expected_runtime"] == RUNTIME
    assert "Copy the main record." in body["schema"]["properties"]["price"]["description"]
    assert "synthetic-secret" not in str(result)
    assert normalize("crawl4ai-json", result["response"])["price"] == "12.99"


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"endpoint": "https://extract.example"},
        {"endpoint": "https://extract.example", "runtime_identity": {"engine": "crawl4ai"}},
    ],
)
def test_run_requires_a_frozen_model_and_implementation(options):
    with pytest.raises(ValueError, match="runtime identity"):
        validate_native_options("crawl4ai-json", options)


@pytest.mark.parametrize("advertised", [True, False])
def test_new_self_hosted_registry_entry_selects_routing_and_projection(monkeypatch, advertised):
    from harness.web_extract.pipeline import METHODS

    method = "synthetic-self-hosted"
    monkeypatch.setitem(METHODS, method, {**METHODS["crawl4ai-json"], "engine": "synthetic"})
    monkeypatch.setenv("EXTRACTION_API_KEY", "synthetic-secret")
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    options = {
        "endpoint": "https://extract.example",
        "runtime_identity": {**RUNTIME, "engine": "synthetic", "json_keepalive": advertised},
        "keepalive": True,
    }
    response = type(
        "Response",
        (),
        {
            "status": 200,
            "headers": {},
            "text": lambda self: '\n{"success":true,"data":{"name":"Ada"}}',
        },
    )()
    with patch("harness.web_extract.pipeline.request", return_value=response) as request:
        if not advertised:
            with pytest.raises(ValueError, match="keepalive"):
                provider_call(
                    method,
                    "<h1>Ada</h1>",
                    "https://original.example/",
                    "https://snapshots.example/frozen",
                    native_options=options,
                    schema=schema,
                )
            request.assert_not_called()
            return
        result = provider_call(
            method,
            "<h1>Ada</h1>",
            "https://original.example/",
            "https://snapshots.example/frozen",
            native_options=options,
            schema=schema,
        )
    assert request.call_args.args == ("https://extract.example/extract/synthetic?keepalive=true",)
    assert normalize(method, result["response"], schema=schema) == {"name": "Ada"}


def test_post_extraction_runtime_check_detects_a_changed_local_model(monkeypatch):
    from harness.web_extract import self_hosted

    monkeypatch.setenv("SNAPSHOT_ORIGINS", "https://snapshots.example")
    monkeypatch.setenv("EXTRACTION_MODEL", "before")
    monkeypatch.setenv("EXTRACTION_MODEL_BASE", "https://model.example")
    monkeypatch.setattr(self_hosted.importlib.metadata, "version", lambda name: "synthetic")
    monkeypatch.setattr(self_hosted, "installed_environment_sha256", lambda: "a" * 64)
    self_hosted.local_runtime_identity.cache_clear()

    async def changed(value):
        monkeypatch.setenv("EXTRACTION_MODEL", "after")
        return {"name": "Ada"}, {}

    try:
        expected = self_hosted.runtime_identity("crawl4ai")
        with patch.object(self_hosted, "crawl", side_effect=changed):
            result = self_hosted.worker(
                {
                    "url": "https://snapshots.example/frozen",
                    "schema": SCHEMA,
                    "engine": "crawl4ai",
                    "expected_runtime": expected,
                }
            )
        assert result["success"] is False
        assert result["error"] == "Runtime changed during extraction"
        assert result["runtime"]["model"] == "after"
    finally:
        self_hosted.local_runtime_identity.cache_clear()


def test_runtime_revalidation_reuses_the_unchanged_browser_version(tmp_path, monkeypatch):
    from harness.web_extract import self_hosted

    browser = tmp_path / "browser"
    browser.write_bytes(b"synthetic browser v1")
    monkeypatch.setenv("EXTRACTION_MODEL", "synthetic")
    monkeypatch.setenv("EXTRACTION_MODEL_BASE", "https://model.example")
    monkeypatch.setenv("CHROME_PATH", str(browser))
    monkeypatch.setattr(self_hosted.importlib.metadata, "version", lambda name: "synthetic")
    monkeypatch.setattr(self_hosted, "installed_environment_sha256", lambda: "a" * 64)
    self_hosted.local_runtime_identity.cache_clear()
    try:
        with patch.object(
            self_hosted.subprocess, "check_output", return_value="Browser 1"
        ) as probe:
            first = self_hosted.runtime_identity("stagehand")
            self_hosted.local_runtime_identity.cache_clear()
            assert self_hosted.runtime_identity("stagehand") == first
            assert probe.call_count == 1
            browser.write_bytes(b"synthetic browser v2")
            self_hosted.local_runtime_identity.cache_clear()
            self_hosted.runtime_identity("stagehand")
            assert probe.call_count == 2
    finally:
        self_hosted.local_runtime_identity.cache_clear()


@pytest.mark.parametrize(
    "status,payload",
    [
        (422, {"success": False, "error": "Schema cannot be extracted"}),
        (200, {"success": False, "error": "Native worker failed"}),
        (200, {"success": True, "data": {"json": {"author": ["Ada", "Grace"], "year": []}}}),
    ],
)
def test_firecrawl_preserves_native_rejection_and_array_values(monkeypatch, status, payload):
    from types import SimpleNamespace

    from harness.web_extract.self_hosted import firecrawl_extract

    monkeypatch.setenv("FIRECRAWL_BACKEND_URL", "http://private-backend.example:3002")
    value = {
        "url": "https://snapshots.example/frozen/book",
        "schema": {
            "type": "object",
            "properties": {"author": {"type": "array"}, "year": {"type": "array"}},
        },
        "instruction": "Copy all authors.",
    }
    response = SimpleNamespace(status_code=status, json=lambda: payload)
    with patch("requests.post", return_value=response) as post:
        data, native = firecrawl_extract(value)
    assert post.call_args.args[0] == "http://private-backend.example:3002/v2/scrape"
    sent = post.call_args.kwargs["json"]
    assert sent["url"] == value["url"]
    assert sent["formats"][0]["schema"] == value["schema"]
    assert sent["formats"][0]["prompt"] == value["instruction"]
    if status == 200 and payload["success"]:
        assert data == {"author": ["Ada", "Grace"], "year": []}
        assert native == payload
    else:
        assert data is None
        assert native == {"http_status": status, "response": payload}


def test_health_and_worker_environment_identity_survives_different_entrypoints(tmp_path):
    import subprocess
    import sys

    from harness.web_extract.self_hosted import installed_environment_sha256

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from harness.web_extract.self_hosted import installed_environment_sha256; "
            "print(installed_environment_sha256())",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == installed_environment_sha256()


@pytest.mark.parametrize("status", [200, 302, 404])
@pytest.mark.parametrize("invalid_utf8", [False, True])
def test_graph_extracts_unchanged_snapshot_html_without_native_url_preprocessing(
    monkeypatch, status, invalid_utf8
):
    import hashlib
    import sys
    from types import ModuleType, SimpleNamespace
    from unittest.mock import Mock

    from harness.web_extract.self_hosted import graph

    source = '<html><a href="/item">Café</a><p>Price: 12</p></html>'.encode()
    if invalid_utf8:
        source += b"\xff"
    modules = {
        name: ModuleType(name)
        for name in (
            "json_schema_to_pydantic",
            "langchain_openai",
            "scrapegraphai",
            "scrapegraphai.graphs",
        )
    }
    modules["json_schema_to_pydantic"].create_model = lambda schema: schema
    modules["langchain_openai"].ChatOpenAI = Mock()
    constructor = Mock()
    constructor.return_value.run.return_value = {"price": 12}
    constructor.return_value.get_execution_info.return_value = []
    modules["scrapegraphai.graphs"].SmartScraperGraph = constructor
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    for key, value in {
        "EXTRACTION_MODEL": "model",
        "EXTRACTION_MODEL_API_KEY": "key",
        "EXTRACTION_MODEL_BASE": "https://model.example",
    }.items():
        monkeypatch.setenv(key, value)
    request = {"url": "https://snapshots.example/item", "schema": SCHEMA}
    with patch(
        "requests.get", return_value=SimpleNamespace(status_code=status, content=source)
    ) as get:
        if status != 200:
            with pytest.raises(ValueError, match=f"Snapshot returned HTTP {status}"):
                graph(request)
            constructor.assert_not_called()
        elif invalid_utf8:
            with pytest.raises(UnicodeDecodeError):
                graph(request)
            constructor.assert_not_called()
        else:
            data, native = graph(request)
            assert data == {"price": 12}
            assert constructor.call_args.kwargs["source"] == source.decode()
            assert native["input_html_sha256"] == hashlib.sha256(source).hexdigest()
        assert get.call_args.args[0] == request["url"]
        assert get.call_args.kwargs["allow_redirects"] is False
