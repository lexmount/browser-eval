"""Multi-domain native extraction, frozen references and unchanged product behavior."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harness.web_scraper import certification, dataset, pipeline, swde


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
    monkeypatch.setitem(
        dataset.BENCHMARKS, "swde", {"profile": str(card), "module": "harness.web_scraper.swde"}
    )
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
    original_scores = (run / "scores.json").read_bytes()
    reprojected = pipeline.score_run(
        home, run, reproject=True, output=tmp_path / "reprojected.json"
    )
    assert reprojected["scores"] == result["scores"]
    assert reprojected["projection_revision"]["changed_cases"] == []
    assert set(reprojected["projection_revision"]["regression_ids"]) == set(
        pipeline.RECORD_PROJECTION_REGRESSIONS
    )
    assert (run / "scores.json").read_bytes() == original_scores
    assert len(calls) == 2


@pytest.mark.parametrize(
    "missing",
    ["schema", "task_adapter_sha256", "dataset_profile_sha256", "dataset_registry_sha256"],
)
def test_record_certification_requires_every_frozen_binding_before_writing(
    inputs, tmp_path, monkeypatch, missing
):
    home, _ = inputs
    path = tmp_path / "plan.json"
    plan = certification.create_plan(home, ["cloudflare-json"], path)
    plan.pop(missing)
    pipeline.save_json(path, plan)
    dispatch = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(certification, "probe", dispatch)
    target = tmp_path / "execution"
    with pytest.raises(ValueError, match="Frozen.*(adapter|schema)"):
        certification.run(home, path, "cloudflare-json", target)
    dispatch.assert_not_called()
    assert not target.exists()


@pytest.mark.parametrize(
    "base",
    [
        "https://user:password@fixtures.example",
        "https://fixtures.example?api_key=private",
        "https://fixtures.example?X-Amz-Signature=private",
        "https://fixtures.example#token=private",
    ],
)
def test_fixture_credentials_never_reach_precall_receipts(inputs, tmp_path, monkeypatch, base):
    home, _ = inputs
    path = tmp_path / "plan.json"
    certification.create_plan(home, ["browserbase-json"], path)
    dispatch = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(certification, "probe", dispatch)
    target = tmp_path / "execution"
    with pytest.raises(ValueError, match="credentials, query or fragment"):
        certification.run(home, path, "browserbase-json", target, fixture_base=base)
    assert not target.exists()
    dispatch.assert_not_called()


@pytest.mark.parametrize("full", [False, True])
def test_record_compatibility_and_full_runs_budget_the_complete_method_matrix(
    inputs, tmp_path, monkeypatch, full
):
    home, _ = inputs
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "fixture")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fixture")
    monkeypatch.setenv("ZYTE_API_KEY", "fixture")
    calls = []

    def respond(method, *args, **kwargs):
        calls.append((method, kwargs["schema"]))
        value = {field: ["value"] for field in kwargs["schema"]["properties"]}
        payload = (
            {"result": value}
            if method == "cloudflare-json"
            else {"customAttributes": {"values": value}}
        )
        return {"http_status": 200, "response": payload}

    monkeypatch.setattr(pipeline, "provider_call", respond)
    target = pipeline.probe(
        home, ["cloudflare-json", "zyte-custom"], full=full, run_dir=tmp_path / "run"
    )
    contract, rows = pipeline.load_run(target)
    assert len(calls) == len(rows) == 4
    assert contract["maximum_physical_calls"] == 24
    assert set(contract["case_ids"]) == {"book-a-0001", "job-b-0002"}
    assert {tuple(schema["properties"]) for _, schema in calls} == {("author",), ("title",)}
    with pytest.raises(ValueError, match="Physical call budget"):
        pipeline.probe(
            home,
            ["cloudflare-json", "zyte-custom"],
            full=full,
            run_dir=tmp_path / "too-small",
            maximum_physical_calls=3,
        )
    assert len(calls) == 4


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


@pytest.mark.parametrize("unannotated_output", [[], ["V6"], "wrong type"])
def test_explicitly_unannotated_groups_are_unavailable_for_every_method(unannotated_output):
    truth = {
        "one": {"domain": "auto", "site": "a", "values": {"model": ["Car"], "engine": []}},
        "two": {"domain": "auto", "site": "a", "values": {"model": [], "engine": []}},
    }
    predictions = {
        "one": {"model": ["Car"], "engine": unannotated_output},
        "two": {"model": ["invented"], "engine": unannotated_output},
    }
    result = swde.score(truth, predictions, unavailable_reference_groups=[("auto", "a", "engine")])
    assert result["page_hit_macro"]["cases"] == 2
    assert result["page_hit_macro"]["site_field_groups"] == 1
    assert result["site_fields"][0]["predicted_pages"] == 2
    assert result["value_micro"]["false_positive"] == 1
    assert result["exact_field_sets"]["total"] == 2
    assert result["reference_coverage"]["unscored_page_fields"] == 2
    with pytest.raises(ValueError, match="populated reference"):
        swde.score(truth, predictions, unavailable_reference_groups=[("auto", "a", "model")])
    with pytest.raises(ValueError, match="outside the declared"):
        swde.score(truth, predictions, unavailable_reference_groups=[("auto", "other", "model")])


def test_wholly_unavailable_reference_returns_no_quality_score():
    truth = {"one": {"domain": "auto", "site": "a", "values": {"engine": []}}}
    result = swde.score(
        truth, {"one": {"engine": ["V6"]}}, unavailable_reference_groups=[("auto", "a", "engine")]
    )
    assert result["page_hit_macro"]["f1"] is None
    assert result["domains"]["auto"]["f1"] is None
    assert result["value_micro"]["f1"] is None
    assert result["exact_field_sets"]["rate"] is None


@pytest.mark.parametrize(
    "source,label,expected,changed",
    [
        ("<p>AT&amp;T</p>", "AT&amp;T", "AT&T", True),
        ("<p>AT&amp;amp;T</p>", "AT&amp;T", "AT&amp;T", False),
        ("<p>Bold title</p>", "<b>Bold</b> title", "Bold title", True),
        ("<p>Other</p>", "AT&amp;T", "AT&amp;T", False),
        ("<script>AT&T</script><p>Other</p>", "AT&amp;T", "AT&amp;T", False),
        ("<p>Démon</p>", "Démon", "Démon", False),
    ],
)
def test_reference_text_changes_require_exact_source_nodes(source, label, expected, changed):
    from copy import deepcopy

    truth = {"one": {"domain": "book", "site": "a", "values": {"title": [label]}}}
    original = deepcopy(truth)
    result = swde.qualify_reference_text(truth, {"one": source.encode()})
    assert result["references"]["one"]["values"]["title"] == [expected]
    assert bool(result["changes"]) is changed
    assert truth == original
    if changed:
        assert result["changes"][0]["source_sha256"] == pipeline.digest(source.encode())
        assert result["changes"][0]["nodes"]
    with pytest.raises(ValueError, match="exact frozen HTML"):
        swde.qualify_reference_text(truth, {})


@pytest.mark.parametrize(
    "actual,hit",
    [
        (" Démon  € 9.99 ", True),
        ("Dmon € 9.99", False),
        ("Démon 9.99", False),
        ("démon € 9.99", False),
    ],
)
def test_whitespace_matching_preserves_accents_units_and_case(actual, hit):
    truth = {"one": {"domain": "book", "site": "a", "values": {"title": ["Démon\u00a0€ 9.99"]}}}
    prediction = {"one": {"title": [actual]}}
    result = swde.score(truth, prediction, text_matching="whitespace")
    assert bool(result["site_fields"][0]["hit_pages"]) is hit
    assert prediction["one"]["title"] == [actual]


def test_partial_labels_need_field_role_adjudication_not_suffix_matching():
    truth = {"one": {"domain": "movie", "site": "amctv", "values": {"director": ["Reitherman"]}}}
    page = b"<p>Director: <a>Wolfgang Reitherman</a></p><p>Cast: <a>Bruce Reitherman</a></p>"
    result = swde.qualify_reference_text(truth, {"one": page})
    assert result["unmatched"] == [
        {"case_id": "one", "field": "director", "original": "Reitherman"}
    ]
    assert result["changes"] == []
    adjudicated = {
        "one": {"domain": "movie", "site": "amctv", "values": {"director": ["Wolfgang Reitherman"]}}
    }
    for name, hit in [("Wolfgang Reitherman", True), ("Bruce Reitherman", False)]:
        scored = swde.score(adjudicated, {"one": {"director": [name]}})
        assert bool(scored["site_fields"][0]["hit_pages"]) is hit


@pytest.mark.parametrize("method", swde.generic_methods())
def test_custom_schema_projection_retains_fields_and_invalid_types(method):
    value = {"author": ["Ada"], "year": "invalid scalar", "extra": 123}
    payload = {
        "cloudflare-json": {"result": value},
        "firecrawl-json": {"data": {"json": value}},
        "firecrawl-parse-json": {"data": {"json": value}},
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


def test_firecrawl_parse_upload_preserves_html_and_requests_native_json(monkeypatch):
    from email.parser import BytesParser
    from email.policy import default

    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-firecrawl-key-only")
    html = "<html><p>作者：Ada</p></html>"
    schema = {
        "type": "object",
        "properties": {"author": {"type": "array", "items": {"type": "string"}}},
        "required": ["author"],
    }
    captured = []

    def request(url, *, body, headers, **kwargs):
        assert "json_body" not in kwargs
        assert url == "https://api.firecrawl.dev/v2/parse"
        message = BytesParser(policy=default).parsebytes(
            ("Content-Type: " + headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
            + body
        )
        parts = {p.get_param("name", header="content-disposition"): p for p in message.iter_parts()}
        assert parts["file"].get_filename() == "snapshot.html"
        assert parts["file"].get_payload(decode=True) == html.encode("utf-8")
        captured.append(json.loads(parts["options"].get_payload(decode=True)))
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps(
                {
                    "success": True,
                    "data": {"json": {"author": ["Ada"]}, "metadata": {"creditsUsed": 5}},
                }
            ),
        )

    monkeypatch.setattr(pipeline, "request", request)
    row = pipeline.provider_call(
        "firecrawl-parse-json", html, "https://source.example/book", schema=schema
    )
    assert captured == [
        {
            "formats": [{"type": "json", "schema": schema}],
            "onlyMainContent": False,
            "timeout": 120000,
        }
    ]
    assert row["request"]["input"] == "html"
    assert row["provider_usage"]["parse_metadata"]["creditsUsed"] == 5
    assert pipeline.normalize("firecrawl-parse-json", row["response"], schema=schema) == {
        "author": ["Ada"]
    }


@pytest.mark.parametrize("method", ["zyte-custom", "firecrawl-parse-json"])
def test_schema_only_interfaces_are_rejected_before_any_dispatch(tmp_path, monkeypatch, method):
    from harness.web_scraper.example import create

    home = create(tmp_path / "example")
    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    with pytest.raises(ValueError, match="dataset-provided custom schema"):
        certification.create_plan(home, [method], tmp_path / "plan.json")
    with pytest.raises(ValueError, match="dataset-provided custom schema"):
        pipeline.probe(home, [method], full=True, run_dir=tmp_path / "calls")
    call.assert_not_called()


def test_missing_schema_dependency_fails_before_paid_dispatch(inputs, monkeypatch, tmp_path):
    import builtins

    home, _ = inputs
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "jsonschema":
            raise ModuleNotFoundError("jsonschema unavailable")
        return original(name, *args, **kwargs)

    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError, match="jsonschema unavailable"):
        pipeline.probe(home, ["cloudflare-json"], full=True, run_dir=tmp_path / "execution")
    call.assert_not_called()
    assert not (tmp_path / "execution/inflight.json").exists()


def test_registered_adapter_dispatch_does_not_depend_on_dataset_name(inputs, monkeypatch, tmp_path):
    home, _ = inputs
    manifest = pipeline.read_json(home / "dataset/manifest.json")
    manifest["benchmark"] = "another-schema-cohort"
    pipeline.save_json(home / "dataset/manifest.json", manifest)
    monkeypatch.setitem(
        dataset.BENCHMARKS, "another-schema-cohort", dict(dataset.BENCHMARKS["swde"])
    )
    plan = certification.create_plan(home, ["cloudflare-json"], tmp_path / "registered.json")
    assert plan["case_count"] == 2
    assert plan["dataset_registry_sha256"] == dataset.registry_sha256()
    scores = pipeline.score_rows(
        home,
        pipeline.read_json(home / "dataset/dataset/ground-truth.json"),
        [
            {"method": "cloudflare-json", "case_id": cid, "http_status": 422}
            for cid in pipeline.read_json(home / "dataset/dataset/ground-truth.json")
        ],
        ["cloudflare-json"],
    )
    assert "page_hit_macro" in scores["cloudflare-json"]


def test_swde_prepare_cannot_reuse_a_valid_other_benchmark(tmp_path, monkeypatch):
    from harness.web_scraper.example import create

    home = create(tmp_path / "product")
    monkeypatch.setitem(
        dataset.BENCHMARKS,
        "zyte",
        {
            **dataset.BENCHMARKS["zyte"],
            "profile": str(home / "profile.json"),
        },
    )
    archive = tmp_path / "archive.bin"
    archive.write_bytes(b"pinned archive")
    card = pipeline.read_json(swde.CARD)
    card["source"]["archive_sha256"] = pipeline.digest(archive.read_bytes())
    card_path = tmp_path / "swde-card.json"
    pipeline.save_json(card_path, card)
    monkeypatch.setattr(swde, "CARD", card_path)
    before = (home / "dataset/manifest.json").read_bytes()
    with pytest.raises(ValueError, match="selected pinned cohort|Pinned"):
        swde.prepare(home, archive)
    assert (home / "dataset/manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    "unsafe", ["source", "source/archives", "source/archives/auto.7z", "source/markuplm-eval.py"]
)
def test_swde_prepare_rejects_linked_storage_before_download_or_write(
    tmp_path, monkeypatch, unsafe
):
    import io
    import tarfile

    from harness.web_fetch.providers.http_client import HttpResponse

    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (outside / "auto.7z").write_bytes(b"outside sentinel")
    linked = home / unsafe
    linked.parent.mkdir(parents=True, exist_ok=True)
    directory = unsafe in ("source", "source/archives")
    linked.symlink_to(outside if directory else outside / "auto.7z", target_is_directory=directory)
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        item = tarfile.TarInfo("source/auto.7z")
        item.size = 3
        bundle.addfile(item, io.BytesIO(b"new"))
    scorer = b"pinned scorer"
    card = pipeline.read_json(swde.CARD)
    card["source"].update(
        archive_sha256=pipeline.digest(archive.read_bytes()), scorer_sha256=pipeline.digest(scorer)
    )
    card_path = tmp_path / "card.json"
    pipeline.save_json(card_path, card)
    monkeypatch.setattr(swde, "CARD", card_path)
    download = Mock(return_value=HttpResponse(200, "https://source.example", {}, scorer))
    monkeypatch.setattr("harness.web_fetch.providers.http_client.request", download)
    with pytest.raises(ValueError, match="symlink"):
        swde.prepare(home, archive)
    assert (outside / "auto.7z").read_bytes() == b"outside sentinel"
    assert not (home / "dataset").exists()
    download.assert_not_called()


def test_clean_prepare_downloads_pinned_scorer_and_recovers_after_partial_extraction(
    tmp_path, monkeypatch
):
    import io
    import tarfile

    from harness.web_fetch.providers.http_client import HttpResponse

    home = tmp_path / "clean"
    archive = tmp_path / "source.tar.gz"
    scorer = b"# synthetic original evaluator\n"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in [
            *[d + ".7z" for d in swde.DOMAINS],
            "groundtruth.7z",
            "readme.txt",
            "license.json",
        ]:
            raw = b"synthetic"
            item = tarfile.TarInfo("source/" + name)
            item.size = len(raw)
            bundle.addfile(item, io.BytesIO(raw))
    card = tmp_path / "card.json"
    pipeline.save_json(
        card,
        {
            "source": {
                "archive_sha256": pipeline.digest(archive.read_bytes()),
                "scorer_sha256": pipeline.digest(scorer),
                "scorer_download_url": "https://source.example/pinned-evaluator",
            },
            "dataset": {
                "commit": "a" * 40,
                "cases": 80,
                "files": {"evaluate.py": pipeline.digest(scorer)},
            },
            "selection": {"pages_per_site": 1},
        },
    )
    monkeypatch.setattr(swde, "CARD", card)
    monkeypatch.setitem(
        dataset.BENCHMARKS, "swde", {"profile": str(card), "module": "harness.web_scraper.swde"}
    )
    download = Mock(
        return_value=HttpResponse(200, "https://source.example/pinned-evaluator", {}, scorer)
    )
    monkeypatch.setattr("harness.web_fetch.providers.http_client.request", download)
    fail = [True]

    def unpack(source, target):
        if source.stem == "groundtruth":
            for domain in swde.DOMAINS:
                for i in range(10):
                    path = target / "groundtruth" / f"{domain}-site{i}-value.txt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("header\nstats\n0001\t1\tA value\n", encoding="utf-8")
        else:
            if source.stem == "book" and fail[0]:
                fail[0] = False
                raise RuntimeError("Synthetic interrupted archive extraction")
            for i in range(10):
                path = target / f"{source.stem}-site{i}(1)" / "0001.htm"
                path.parent.mkdir(parents=True)
                path.write_text(
                    f'<base href="https://{source.stem}.example/{i}"><p>A value</p>',
                    encoding="utf-8",
                )

    monkeypatch.setattr(swde, "_extract", unpack)
    with pytest.raises(RuntimeError, match="interrupted archive extraction"):
        swde.prepare(home, archive)
    assert not (home / "dataset").exists()
    result = swde.prepare(home, archive)
    assert result["cases"] == 80
    assert len(list((home / "dataset/html").glob("*.html"))) == 80
    assert (home / "dataset/evaluate.py").read_bytes() == scorer
    assert swde.prepare(home, archive)["cases"] == 80
    assert download.call_count == 1


def test_clean_prepare_rejects_wrong_scorer_before_extracting(tmp_path, monkeypatch):
    home = tmp_path / "clean"
    source = tmp_path / "source.tar.gz"
    source.write_bytes(b"archive")
    scorer = home / "source/markuplm-eval.py"
    scorer.parent.mkdir(parents=True)
    scorer.write_bytes(b"wrong evaluator")
    card = tmp_path / "card.json"
    pipeline.save_json(
        card,
        {
            "source": {
                "archive_sha256": pipeline.digest(source.read_bytes()),
                "scorer_sha256": pipeline.digest(b"expected"),
            }
        },
    )
    monkeypatch.setattr(swde, "CARD", card)
    extract = Mock()
    monkeypatch.setattr(swde, "_extract", extract)
    with pytest.raises(ValueError, match="evaluator changed"):
        swde.prepare(home, source)
    extract.assert_not_called()
    assert not (home / "dataset").exists()


@pytest.mark.parametrize("interface", ["report", "assemble"])
def test_product_interfaces_reject_other_field_families_before_reading_artifacts(
    inputs, tmp_path, interface
):
    from harness.web_scraper import report, review

    home, _ = inputs
    target = tmp_path / "not-created"
    missing = tmp_path / "absent-input"
    with pytest.raises(ValueError, match="requires product references"):
        if interface == "report":
            report.build(target, missing, home=home)
        else:
            review.assemble(home, missing, missing, missing, missing, target, profile=missing)
    assert not target.exists()


def test_record_review_missing_content_fails_before_creating_work(inputs, tmp_path):
    from harness.web_scraper import review

    home, _ = inputs
    target = tmp_path / "not-created"
    with pytest.raises(FileNotFoundError):
        review.prepare(home, tmp_path / "absent-input", target)
    assert not target.exists()


@pytest.mark.parametrize("change", ["none", "missing", "before", "during", "certificate"])
def test_record_repair_binds_adapter_before_resume_after_calls_and_on_read(
    inputs, tmp_path, monkeypatch, change
):
    from harness.web_scraper import repair

    home, _ = inputs
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "fixture")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fixture")
    monkeypatch.setattr(
        pipeline,
        "provider_call",
        Mock(
            side_effect=[
                {"http_status": 500, "response": {"error": "temporary origin error"}},
                {"http_status": 200, "response": {"result": {"title": ["Engineer"]}}},
            ]
        ),
    )
    plan = tmp_path / "parent-plan.json"
    certification.create_plan(home, ["cloudflare-json"], plan)
    parent = tmp_path / "parent"
    certification.run(home, plan, "cloudflare-json", parent)
    content = tmp_path / "repair-content.json"
    pipeline.save_json(content, {"evidence_runs": [{"run": str(parent)}]})
    path = tmp_path / "repair-plan.json"
    frozen = repair.create_plan(home, content, path, {}, None)
    bindings = dataset.task_bindings(home)
    changed = {**bindings, "task_adapter_sha256": "f" * 64}
    if change == "missing":
        frozen.pop("task_adapter_sha256")
        pipeline.save_json(path, frozen)
    if change == "before":
        monkeypatch.setattr(repair, "task_bindings", lambda _: changed)

    def respond(*args, **kwargs):
        if change == "during":
            monkeypatch.setattr(repair, "task_bindings", lambda _: changed)
        return {"http_status": 200, "response": {"result": {"author": ["Ada"]}}}

    dispatch = Mock(side_effect=respond)
    monkeypatch.setattr(pipeline, "provider_call", dispatch)
    monkeypatch.setattr(repair, "load_runtime_env", lambda: None)
    target = tmp_path / "repair"
    if change in ("missing", "before", "during"):
        with pytest.raises(ValueError, match="repair task adapter|Repair task adapter"):
            repair.run(home, path, "cloudflare-json", target)
        assert dispatch.call_count == (1 if change == "during" else 0)
        assert not (target / "certificate.json").exists()
        return
    repair.run(home, path, "cloudflare-json", target)
    repair.run(home, path, "cloudflare-json", target)
    assert dispatch.call_count == 1
    contract = pipeline.read_json(target / "contract.json")
    certificate = pipeline.read_json(target / "certificate.json")
    assert contract["schema"] == "failed-extraction-repair-run/2"
    for key, value in bindings.items():
        assert contract[key] == certificate[key] == value
    if change == "certificate":
        certificate["task_adapter_sha256"] = "f" * 64
        pipeline.save_json(target / "certificate.json", certificate)
    revision = {
        "path": str(target),
        "sha256": pipeline.digest((target / "certificate.json").read_bytes()),
    }
    if change == "certificate":
        with pytest.raises(ValueError, match="task adapter or dataset binding"):
            repair.verify_repair(home, parent, revision)
    else:
        _, rows = repair.load_selection(home, {"run": str(parent), "repair": [revision]})
        by_case = {row["case_id"]: row for row in rows}
        assert by_case["book-a-0001"]["normalized_fields"] == {"author": ["Ada"]}
        assert by_case["job-b-0002"]["normalized_fields"] == {"title": ["Engineer"]}


def test_invalid_schema_is_rejected_before_any_paid_dispatch(inputs, monkeypatch, tmp_path):
    from jsonschema.exceptions import SchemaError

    home, schemas = inputs
    schemas["book"]["properties"]["author"]["type"] = "invalid-type"
    path = home / "dataset/schemas.json"
    pipeline.save_json(path, schemas)
    manifest = pipeline.read_json(home / "dataset/manifest.json")
    manifest["files"]["schemas.json"] = {
        "sha256": pipeline.digest(path.read_bytes()),
        "bytes": path.stat().st_size,
    }
    pipeline.save_json(home / "dataset/manifest.json", manifest)
    card = pipeline.read_json(swde.CARD)
    card["dataset"]["inventory_sha256"] = pipeline.digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    pipeline.save_json(swde.CARD, card)
    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    with pytest.raises(SchemaError):
        pipeline.probe(home, ["cloudflare-json"], full=True, run_dir=tmp_path / "execution")
    call.assert_not_called()
