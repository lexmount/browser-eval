"""Multi-domain native extraction, frozen references and unchanged product behavior."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harness.web_scraper import certification, pipeline, swde


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    home = tmp_path / "data"
    root = home / "dataset"
    (root / "html").mkdir(parents=True)
    (root / "dataset").mkdir()
    truth = {
        "book-a-0001": {
            "domain": "book",
            "site": "a",
            "url": "https://book.example/one",
            "values": {"author": ["GOLD_DO_NOT_SEND"]},
        },
        "job-b-0002": {
            "domain": "job",
            "site": "b",
            "url": "https://job.example/two",
            "values": {"title": ["Engineer"]},
        },
    }
    schemas = {
        domain: {
            "type": "object",
            "properties": {field: {"type": "array", "items": {"type": "string"}}},
            "required": [field],
            "additionalProperties": False,
        }
        for domain, field in [("book", "author"), ("job", "title")]
    }
    for case in truth:
        (root / "html" / (case + ".html")).write_text("<p>Ada Engineer</p>", encoding="utf-8")
    pipeline.save_json(root / "dataset/ground-truth.json", truth)
    pipeline.save_json(root / "schemas.json", schemas)
    (root / "evaluate.py").write_text("# pinned original page-hit evaluator\n", encoding="utf-8")
    files = {
        p.relative_to(root).as_posix(): {
            "sha256": pipeline.digest(p.read_bytes()),
            "bytes": p.stat().st_size,
        }
        for p in root.rglob("*")
        if p.is_file()
    }
    pipeline.save_json(
        root / "manifest.json",
        {"benchmark": "swde", "commit": "b" * 40, "cases": 2, "files": files},
    )
    card = tmp_path / "swde.json"
    pipeline.save_json(
        card,
        {
            "dataset": {
                "commit": "b" * 40,
                "cases": 2,
                "inventory_sha256": pipeline.digest(
                    json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
                ),
                "files": {"evaluate.py": files["evaluate.py"]["sha256"]},
            }
        },
    )
    monkeypatch.setattr(swde, "CARD", card)
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token-only")
    return home, schemas


@pytest.mark.parametrize("static_controls", [False, True])
def test_certified_multi_domain_run_uses_task_schema_and_never_sends_labels(
    inputs, tmp_path, monkeypatch, static_controls
):
    home, schemas = inputs
    calls = []

    def request(url, *, json_body, **kwargs):
        calls.append(json_body)
        field = json_body["response_format"]["json_schema"]["required"][0]
        native = {"success": True, "result": {field: ["Ada" if field == "author" else "Engineer"]}}
        return SimpleNamespace(status=200, headers={}, text=lambda: json.dumps(native))

    monkeypatch.setattr(pipeline, "request", request)
    plan = tmp_path / "plan.json"
    frozen = certification.create_plan(home, ["cloudflare-json"], plan)
    if static_controls:
        frozen["participants"]["cloudflare-json"]["native_options"] = {
            "setJavaScriptEnabled": False,
            "rejectRequestPattern": [".*"],
        }
        pipeline.save_json(plan, frozen)
    run = tmp_path / "run"
    result = certification.run(home, plan, "cloudflare-json", run)
    assert result["cases"] == 2
    _, rows = pipeline.load_run(run)
    assert len(calls) == 2
    if static_controls:
        assert all(
            c["setJavaScriptEnabled"] is False and c["rejectRequestPattern"] == [".*"]
            for c in calls
        )
        assert (
            pipeline.read_json(run / "contract.json")["native_options"]
            == frozen["participants"]["cloudflare-json"]["native_options"]
        )
    assert [c["response_format"]["json_schema"] for c in calls] == [schemas["book"], schemas["job"]]
    assert "GOLD_DO_NOT_SEND" not in json.dumps(calls)
    assert all(r["schema_valid"] and r["elapsed_seconds"] >= 0 for r in rows)
    assert rows[0]["normalized_fields"] == {"author": ["Ada"]}
    assert result["scores"]["cloudflare-json"]["page_hit_macro"]["cases"] == 2
    assert (run / "certificate.json").is_file()
    certification.run(home, plan, "cloudflare-json", run)
    assert len(calls) == 2  # Completed results are never rerun.


def test_changed_reference_stops_before_any_provider_call(inputs, tmp_path, monkeypatch):
    home, _ = inputs
    plan = tmp_path / "plan.json"
    certification.create_plan(home, ["cloudflare-json"], plan)
    (home / "dataset/html/book-a-0001.html").write_text("changed", encoding="utf-8")
    dispatch = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(pipeline, "provider_call", dispatch)
    with pytest.raises(ValueError, match="Dataset bytes changed"):
        certification.run(home, plan, "cloudflare-json", tmp_path / "run")
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "method", ["zyte-product", "diffbot-product", "scrapfly-product", "extruct"]
)
def test_product_only_interfaces_cannot_claim_swde_schema_support(inputs, tmp_path, method):
    home, _ = inputs
    with pytest.raises(ValueError, match="arbitrary-schema"):
        certification.create_plan(home, [method], tmp_path / "plan.json")
    assert not (tmp_path / "plan.json").exists()


@pytest.mark.parametrize(
    "prediction,hit,exact",
    [
        ({"author": ["Ada"]}, True, False),
        ({"author": ["Ada", "Bob"]}, True, True),
        ({"author": ["Eve"]}, False, False),
        ({"author": []}, False, False),
        ({"author": "Ada"}, False, False),
    ],
)
def test_multi_value_page_hit_does_not_imply_complete_extraction(prediction, hit, exact):
    truth = {"one": {"domain": "book", "site": "a", "values": {"author": ["Ada", "Bob"]}}}
    score = swde.score(truth, {"one": prediction})
    assert bool(score["site_fields"][0]["hit_pages"]) is hit
    assert bool(score["exact_field_sets"]["correct"]) is exact
    if prediction == {"author": ["Ada"]}:
        assert score["value_micro"]["recall"] == 0.5


def test_empty_gold_false_positive_stays_in_the_frozen_population():
    truth = {"one": {"domain": "job", "site": "a", "values": {"title": []}}}
    result = swde.score(truth, {"one": {"title": ["invented"]}})
    assert result["site_fields"][0]["predicted_pages"] == 1
    assert result["site_fields"][0]["hit_pages"] == 0
    assert result["value_micro"]["false_positive"] == 1
    assert result["page_hit_macro"]["site_field_groups"] == 1


@pytest.mark.parametrize("method", swde.generic_methods())
def test_custom_schema_projection_retains_fields_and_invalid_types(method):
    value = {"author": ["Ada"], "year": "invalid scalar", "extra": 123}
    payload = {
        "cloudflare-json": {"result": value},
        "firecrawl-json": {"data": {"json": value}},
        "browserbase-json": {"statusCode": 200, "content": value},
        "oxylabs-json": {"results": [{"json": value}]},
        "scrapegraph-extract": {"json": value},
        "zyte-custom": {"customAttributes": {"values": value}},
    }[method]
    assert pipeline.normalize(method, payload, schema={"type": "object"}) == value


def test_zyte_custom_uses_original_html_and_custom_schema_without_fetching(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "test-zyte-key-only")
    schema = {
        "type": "object",
        "properties": {"author": {"type": "array", "items": {"type": "string"}}},
        "required": ["author"],
        "additionalProperties": False,
    }
    metadata = {
        "inputTokens": 150,
        "outputTokens": 12,
        "textInputTokensBeforeTruncation": 140,
        "textInputTokens": 130,
        "maxInputTokens": 16384,
    }
    requests = []

    def send(url, *, json_body, **kwargs):
        requests.append(json_body)
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps(
                {"customAttributes": {"values": {"author": ["Ada"]}, "metadata": metadata}}
            ),
        )

    monkeypatch.setattr(pipeline, "request", send)
    row = pipeline.provider_call(
        "zyte-custom", "<p>Ada</p>", "https://source.example/book", schema=schema
    )
    assert requests == [
        {
            "url": "https://source.example/book",
            "pageContent": True,
            "pageContentOptions": {"extractFrom": "userHtml", "userHtml": "<p>Ada</p>"},
            "customAttributes": schema["properties"],
        }
    ]
    assert row["request"]["input"] == "html"
    assert row["request"]["schema"] == schema
    assert row["provider_usage"]["custom_attributes"] == metadata
    assert row["elapsed_seconds"] >= 0
    assert pipeline.normalize("zyte-custom", row["response"], schema=schema) == {"author": ["Ada"]}


def test_zyte_custom_native_error_is_not_a_successful_empty_extraction():
    response = {
        "customAttributes": {"values": {}, "metadata": {"error": "extraction/schema-size-exceeded"}}
    }
    assert pipeline.normalize("zyte-custom", response, schema={"type": "object"}) is None


def test_zyte_custom_cannot_run_without_an_explicit_schema(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "test-zyte-key-only")
    request = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(pipeline, "request", request)
    with pytest.raises(ValueError, match="object schema"):
        pipeline.provider_call("zyte-custom", "<p>Ada</p>", "https://source.example/book")
    request.assert_not_called()


@pytest.mark.parametrize("payload", [[], ["unexpected"], "not an object", None])
def test_zyte_custom_keeps_malformed_native_envelopes(monkeypatch, payload):
    monkeypatch.setenv("ZYTE_API_KEY", "test-zyte-key-only")
    monkeypatch.setattr(
        pipeline,
        "request",
        lambda *a, **kw: SimpleNamespace(status=200, headers={}, text=lambda: json.dumps(payload)),
    )
    row = pipeline.provider_call(
        "zyte-custom",
        "<p>Ada</p>",
        "https://source.example/book",
        schema={"type": "object", "properties": {}},
    )
    assert row["response"] == payload
    assert pipeline.normalize("zyte-custom", payload, schema={"type": "object"}) is None
