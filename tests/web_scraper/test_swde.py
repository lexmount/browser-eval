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


def test_certified_multi_domain_run_uses_task_schema_and_never_sends_labels(
    inputs, tmp_path, monkeypatch
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
    certification.create_plan(home, ["cloudflare-json"], plan)
    run = tmp_path / "run"
    result = certification.run(home, plan, "cloudflare-json", run)
    assert result["cases"] == 2
    _, rows = pipeline.load_run(run)
    assert len(calls) == 2
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


@pytest.mark.parametrize("method", swde.GENERIC_METHODS)
def test_custom_schema_projection_retains_fields_and_invalid_types(method):
    value = {"author": ["Ada"], "year": "invalid scalar", "extra": 123}
    payload = {
        "cloudflare-json": {"result": value},
        "firecrawl-json": {"data": {"json": value}},
        "browserbase-json": {"statusCode": 200, "content": value},
        "oxylabs-json": {"results": [{"json": value}]},
        "scrapegraph-extract": {"json": value},
    }[method]
    assert pipeline.normalize(method, payload, schema={"type": "object"}) == value
