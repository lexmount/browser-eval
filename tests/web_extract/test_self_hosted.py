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


@pytest.mark.parametrize("method", ["crawl4ai-json", "scrapegraph-oss-json"])
def test_open_source_projections_keep_missing_and_multiple_field_values(method):
    schema = {
        "type": "object",
        "properties": {"name": {"type": "array"}, "phone": {"type": "array"}},
    }
    data = {"name": ["First", "Second"], "phone": []}
    assert normalize(method, {"success": True, "data": data}, schema=schema) == data
    assert normalize(method, {"success": False, "data": data}, schema=schema) is None
    assert normalize(method, {"success": True, "data": []}, schema=schema) is None


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
