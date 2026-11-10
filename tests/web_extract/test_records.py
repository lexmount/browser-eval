"""Portable field tasks, live-source freshness and captured resource boundaries."""

import json
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.web_extract import analysis, certification, pipeline, records, report, snapshots


def definition(tmp_path, *, mode="live", assets=False, asset_name="image.bin"):
    raw = b"<html><head><title>Profile</title></head><body><h1>Ada</h1></body></html>"
    page = tmp_path / "source.html"
    page.write_bytes(raw)
    values = {"name": ["Ada"]}
    item = {
        "id": "one",
        "url": "https://source.example/person/1",
        "domain": "profile",
        "site": "source",
        "input": str(page),
        "input_sha256": pipeline.digest(raw),
        "values": values,
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "array", "items": {"type": "string"}}},
            "required": ["name"],
        },
        "qualification": {
            "input_sha256": pipeline.digest(raw),
            "reference_sha256": pipeline.digest(
                json.dumps(values, sort_keys=True, ensure_ascii=False).encode()
            ),
        },
    }
    if assets:
        asset = tmp_path / "image.bin"
        asset.write_bytes(b"captured-image")
        item["assets"] = [
            {
                "path": "assets/one/" + asset_name,
                "input": str(asset),
                "sha256": pipeline.digest(asset.read_bytes()),
            }
        ]
    path = tmp_path / "cohort.json"
    pipeline.save_json(
        path,
        {
            "schema": "source-qualified-extraction-cohort/1",
            "id": "sample",
            "name": "Source sample",
            "repository": "example/sample",
            "input_mode": mode,
            "cases": [item],
            "source_versions": {"annotations": "1" * 40},
            "selection": {"seed": "frozen-before-calls"},
            "source_license": "Synthetic fixture only",
        },
    )
    return path


def prepared(tmp_path, **options):
    source = definition(tmp_path, **options)
    home = tmp_path / "home"
    records.prepare(home, source)
    return home


def test_validation_reads_decoding_inventory_once_and_rechecks_the_next_operation(
    tmp_path, monkeypatch
):
    source = definition(tmp_path)
    model = pipeline.read_json(source)
    model["cases"] = [dict(model["cases"][0], id=f"case-{i}") for i in range(8)]
    pipeline.save_json(source, model)
    home = tmp_path / "home"
    records.prepare(home, source)
    original = pipeline.read_json
    reads = []

    def read(path):
        if Path(path).name == "provenance.json":
            reads.append(path)
        return original(path)

    monkeypatch.setattr(pipeline, "read_json", read)
    pipeline.validate_dataset(home)
    assert len(reads) == 1
    pipeline.validate_dataset(home)
    assert len(reads) == 2
    path = home / "dataset/provenance.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Dataset bytes changed"):
        pipeline.validate_dataset(home)


def test_qualified_decoder_rejects_an_unknown_case(tmp_path):
    home = prepared(tmp_path)
    with pytest.raises(KeyError):
        pipeline.captured_html(home, "unknown-case", b"<p>Unbound content</p>")


@pytest.mark.parametrize("policy", [False, True])
@pytest.mark.parametrize("header", [None, "text/html; charset=utf-8", "text/html; charset=unknown"])
@pytest.mark.parametrize("gate", [False, True])
def test_unobservable_live_body_retains_access_evidence_and_bounded_fallback(
    tmp_path, monkeypatch, policy, header, gate
):
    from datetime import datetime

    source = definition(tmp_path)
    model = pipeline.read_json(source)
    if policy:
        model["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
        model["cases"][0]["qualification"].update(
            source_url=model["cases"][0]["url"], observed_at=datetime.now(UTC).isoformat()
        )
    pipeline.save_json(source, model)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(
            status=200, url=url,
            body=(b'<title>Just a moment...</title><form id="challenge-form"></form>'
                  if gate else b"<!-- temporarily unavailable -->"),
            headers={"content-type": header} if header else {},
        ),
    )
    attempt = {"http_status": 200, **(
        {"access_interstitial": True} if gate else
        {"error": "LookupError" if header and header.endswith("unknown") else "ParserError"}
    )}
    if policy:
        receipt = records.qualify_live(home, ["one"])[0]
        assert receipt["verification_mode"] == "independent_captured_reference"
        assert receipt["source_attempts"] == [attempt]
    else:
        with pytest.raises(records.LiveSourceAccessError) as caught:
            records.qualify_live(home, ["one"])
        assert caught.value.source_attempts == [attempt]


def test_runtime_scorer_must_match_the_pinned_portable_evaluator(tmp_path, monkeypatch):
    home = prepared(tmp_path)
    pipeline.validate_dataset(home)
    original = records.exported_evaluator()
    monkeypatch.setattr(records, "exported_evaluator", lambda: original + b"\n# another scorer\n")
    with pytest.raises(ValueError, match="evaluator.*runtime"):
        pipeline.validate_dataset(home)


@pytest.mark.parametrize("prediction,hit", [
    (" \thttps://a.com/x.jpg\n", True),
    (" https://a.com/y.jpg ", False),
    (" /x.jpg ", True),
    (" /x .jpg ", False),
])
def test_url_boundary_whitespace_in_scores_and_details(prediction, hit):
    reference = {"url": "https://a.com/p/", "domain": "profile", "site": "a",
                 "values": {"photo": ["https://a.com/x.jpg"]},
                 "value_kinds": {"photo": "url"}}
    result = records.score({"one": reference}, {"one": {"photo": [prediction]}})
    assert bool(result["value_micro"]["true_positive"]) == hit
    fields = analysis.record_field_results(
        reference, {"http_status": 200, "normalized_fields": {"photo": [prediction]}}
    )
    assert fields["photo"]["exact"] == hit


def test_definition_binds_both_page_bytes_and_reference_before_preparation(tmp_path):
    source = definition(tmp_path)
    value = pipeline.read_json(source)
    value["cases"][0]["values"]["name"] = ["Another person"]
    pipeline.save_json(source, value)
    with pytest.raises(ValueError, match="qualification"):
        records.prepare(tmp_path / "home", source)
    value["cases"][0]["values"]["name"] = ["Ada"]
    pipeline.save_json(source, value)
    Path(value["cases"][0]["input"]).write_text("changed webpage", encoding="utf-8")
    with pytest.raises(ValueError, match="Source HTML"):
        records.prepare(tmp_path / "other", source)


def test_exported_scorer_reproduces_scores_without_delegating_to_current_runtime(
    tmp_path, monkeypatch
):
    import importlib.util

    home = prepared(tmp_path)
    path = home / "dataset/evaluate.py"
    spec = importlib.util.spec_from_file_location("frozen_cohort_scorer", path)
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)

    def changed_runtime(*args, **kwargs):
        raise AssertionError("The frozen evaluator delegated to the current runtime")

    monkeypatch.setattr("harness.web_extract.swde.score", changed_runtime)
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    assert evaluator.score(truth, {"one": {"name": ["Ada"]}})["value_micro"]["f1"] == 1
    assert evaluator.score(truth, {"one": {"name": ["Another person"]}})["value_micro"]["f1"] == 0
    truth["one"]["values"]["name"] = ["Ada Lovelace"]
    assert evaluator.score(truth, {"one": {"name": ["Ada  Lovelace"]}})["value_micro"]["f1"] == 1


@pytest.mark.parametrize("instructions", ["", "  ", False, [], None])
def test_explicit_invalid_instruction_cannot_fall_back_to_another_task(tmp_path, instructions):
    source = definition(tmp_path)
    data = pipeline.read_json(source)
    data["extraction_instructions"] = instructions
    pipeline.save_json(source, data)
    with pytest.raises(ValueError, match="nonempty string"):
        records.prepare(tmp_path / "home", source)


@pytest.mark.parametrize(
    "expected,predicted,match",
    [
        ("https://Alice:Secret@example.org/a", "HTTPS://Alice:Secret@EXAMPLE.ORG/a", True),
        ("https://Alice:Secret@example.org/a", "https://alice:secret@example.org/a", False),
        ("https://example.org/?x=1&copy=1", "https://example.org/?x=1©=1", False),
    ],
)
def test_url_matching_preserves_userinfo_and_literal_query_data(expected, predicted, match):
    reference = {
        "url": "https://example.org/",
        "domain": "page",
        "site": "example",
        "values": {"link": [expected]},
        "value_kinds": {"link": "url"},
    }
    score = records.score({"one": reference}, {"one": {"link": [predicted]}})
    assert (score["value_micro"]["f1"] == 1) is match
    fields = analysis.record_field_results(
        reference, {"http_status": 200, "normalized_fields": {"link": [predicted]}}
    )
    assert fields["link"]["exact"] is match


def test_source_url_attribute_is_decoded_once(tmp_path):
    reference = {
        "url": "https://example.org/",
        "values": {"link": ["https://example.org/?x=1&copy=1"]},
        "value_kinds": {"link": "url"},
    }
    raw = b'<a href="https://example.org/?x=1&amp;copy=1">Original</a>'
    assert records.reference_support(reference, raw) == []
    reference["values"]["link"] = ["https://example.org/?x=1©=1"]
    assert records.reference_support(reference, raw)


def test_live_url_calls_never_substitute_a_hosted_copy_or_send_reference_answers(
    tmp_path, monkeypatch
):
    home = prepared(tmp_path)
    native = []
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-bb")
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(status=200, body=b"<h1>Ada</h1>", url=url),
    )

    def call(url, *, json_body, **kwargs):
        native.append(json_body)
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps({"statusCode": 200, "content": {"name": ["Ada"]}}),
        )

    monkeypatch.setattr(pipeline, "request", call)
    plan = tmp_path / "plan.json"
    certification.create_plan(home, ["browserbase-json"], plan)
    result = certification.run(home, plan, "browserbase-json", tmp_path / "run")
    assert result["cases"] == 1
    assert native[0]["url"] == "https://source.example/person/1"
    assert "Ada" not in json.dumps(native[0])
    _, rows = pipeline.load_run(tmp_path / "run")
    assert rows[0]["request"]["input"] == "url"
    with pytest.raises(ValueError, match="original URLs"):
        certification.create_plan(
            home,
            ["browserbase-json"],
            tmp_path / "bad.json",
            snapshots=tmp_path / "irrelevant.json",
        )


def test_live_report_links_to_original_sources_and_rejects_snapshot_previews(tmp_path):
    home = prepared(tmp_path)
    preview = report.record_preview(home, {}, local_example=False)
    assert preview == {
        "mode": "live",
        "case_count": 1,
        "urls": {"one": "https://source.example/person/1"},
    }
    with pytest.raises(ValueError, match="original URLs"):
        report.record_preview(
            home, {"snapshot_base": "https://hosted.example"}, local_example=False
        )


def test_list_query_preserves_all_requested_records_without_single_record_instructions(
    tmp_path, monkeypatch
):
    source = definition(tmp_path)
    data = pipeline.read_json(source)
    case = data["cases"][0]
    raw = b"<html><h1>People</h1><ul><li>Ada</li><li>Grace</li></ul></html>"
    Path(case["input"]).write_bytes(raw)
    case["input_sha256"] = pipeline.digest(raw)
    case["values"] = {"name": ["Ada", "Grace"]}
    case["qualification"] = {
        "input_sha256": pipeline.digest(raw),
        "reference_sha256": pipeline.digest(json.dumps(case["values"], sort_keys=True).encode()),
    }
    query = "List every person's name on this page."
    case["schema"]["properties"]["name"]["description"] = query
    prompt = "Follow the field question and return every requested record. Copy text exactly."
    data["extraction_instructions"] = prompt
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-bb")
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kwargs: SimpleNamespace(status=200, body=raw, url=url),
    )
    requests = []

    def native(url, *, json_body, **kwargs):
        requests.append(json_body)
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps({"statusCode": 200, "content": case["values"]}),
        )

    monkeypatch.setattr(pipeline, "request", native)
    plan = tmp_path / "plan.json"
    frozen = certification.create_plan(home, ["browserbase-json"], plan)
    result = certification.run(home, plan, "browserbase-json", tmp_path / "run")
    assert frozen["extraction_instructions"] == prompt
    sent = requests[0]["schema"]["properties"]["name"]["description"]
    assert query in sent and prompt in sent
    assert "Exclude navigation and other records" not in sent
    assert result["scores"]["browserbase-json"]["value_micro"]["f1"] == 1


def test_live_source_drift_stops_certification_without_replacing_saved_calls(tmp_path, monkeypatch):
    home = prepared(tmp_path)
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-bb")
    state = {"called": False}

    def source(url, **kwargs):
        return SimpleNamespace(
            status=200, url=url, body=b"<h1>Removed</h1>" if state["called"] else b"<h1>Ada</h1>"
        )

    monkeypatch.setattr("harness.web_fetch.providers.http_client.request", source)

    def call(url, **kwargs):
        state["called"] = True
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps({"statusCode": 200, "content": {"name": ["Ada"]}}),
        )

    monkeypatch.setattr(pipeline, "request", call)
    plan = tmp_path / "plan.json"
    certification.create_plan(home, ["browserbase-json"], plan)
    run = tmp_path / "run"
    with pytest.raises(ValueError, match="no longer supported"):
        certification.run(home, plan, "browserbase-json", run)
    assert list((run / "calls").glob("*.json"))
    assert not (run / "certificate.json").exists()


@pytest.mark.parametrize("status", [200, 429, 503])
def test_empty_or_unavailable_live_source_cannot_qualify(tmp_path, monkeypatch, status):
    home = prepared(tmp_path)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kwargs: SimpleNamespace(status=status, body=b"", url=url),
    )
    with pytest.raises(ValueError, match="Live (source unavailable|reference no longer supported)"):
        records.qualify_live(home, ["one"])


def test_transient_source_failure_can_recover_without_retrying_provider_calls(
    tmp_path, monkeypatch
):
    home = prepared(tmp_path)
    monkeypatch.setattr("time.sleep", lambda _: None)
    responses = iter(
        [
            SimpleNamespace(
                status=503, body=b"", url="https://source.example/person/1", headers={}
            ),
            SimpleNamespace(
                status=200, body=b"<h1>Ada</h1>", url="https://source.example/person/1"
            ),
        ]
    )
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request", lambda *a, **k: next(responses)
    )
    receipt = records.qualify_live(home, ["one"])
    assert receipt[0]["source_attempts"] == [{"http_status": 503}, {"http_status": 200}]


@pytest.mark.parametrize("status", [202, 403, 404, 429, 503])
def test_source_access_failure_does_not_decide_task_validity(tmp_path, monkeypatch, status):
    home = prepared(tmp_path)
    original = (home / "dataset/dataset/ground-truth.json").read_bytes()
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kwargs: SimpleNamespace(status=status, body=b"", url=url),
    )
    with pytest.raises(records.LiveSourceAccessError) as caught:
        records.qualify_live(home, ["one"])
    assert caught.value.validity == "unresolved"
    assert caught.value.case_id == "one"
    assert caught.value.source_attempts[-1] == {"http_status": status}
    assert (home / "dataset/dataset/ground-truth.json").read_bytes() == original


@pytest.mark.parametrize(
    "age,body,status,accepted",
    [
        (60, b"", 429, True),
        (60, b"", 403, True),
        (60, b"", 202, True),
        (7200, b"", 429, False),
        (-60, b"", 429, False),
        (60, b"<p>Someone else</p>", 200, False),
    ],
)
def test_predeclared_recent_independent_observation_preserves_blocked_valid_tasks(
    tmp_path, monkeypatch, age, body, status, accepted
):
    from datetime import datetime, timedelta

    source = definition(tmp_path)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    item["qualification"].update(
        source_url=item["url"],
        observed_at=(datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
    )
    data["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(status=status, body=body, url=url),
    )
    if accepted:
        receipt = records.qualify_live(home, ["one"])[0]
        assert receipt["source_state"] == "access_unavailable"
        assert receipt["verification_mode"] == "independent_captured_reference"
        assert receipt["observed_html_sha256"] == item["input_sha256"]
        assert receipt["source_attempts"][-1] == {"http_status": status}
    else:
        with pytest.raises(ValueError):
            records.qualify_live(home, ["one"])


def test_recent_capture_uses_its_redirect_and_document_base_during_a_block(tmp_path, monkeypatch):
    from datetime import datetime

    source = definition(tmp_path)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    raw = b'<html><base href="images/"><a href="item">Entry</a></html>'
    Path(item["input"]).write_bytes(raw)
    item.update(
        final_url="https://source.example/catalog/index.html",
        values={"name": ["https://source.example/catalog/images/item"]},
        value_kinds={"name": "url"},
        input_sha256=pipeline.digest(raw),
    )
    item["qualification"].update(
        input_sha256=pipeline.digest(raw),
        source_url=item["url"],
        observed_at=datetime.now(UTC).isoformat(),
        reference_sha256=pipeline.digest(
            json.dumps(item["values"], sort_keys=True, ensure_ascii=False).encode()
        ),
    )
    data["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(status=429, body=b"", url=url),
    )
    assert (
        records.qualify_live(home, ["one"])[0]["verification_mode"]
        == "independent_captured_reference"
    )


@pytest.mark.parametrize("status", [429, None])
def test_interrupted_finalization_resumes_a_blocked_source_without_repeating_calls(
    tmp_path, monkeypatch, status
):
    from datetime import datetime

    source = definition(tmp_path)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    item["qualification"].update(source_url=item["url"], observed_at=datetime.now(UTC).isoformat())
    data["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-bb")

    def blocked(url, **kwargs):
        if status is None:
            raise OSError("Temporary network failure")
        return SimpleNamespace(status=status, body=b"", url=url)

    monkeypatch.setattr("harness.web_fetch.providers.http_client.request", blocked)
    calls = []

    def native(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps({"statusCode": 200, "content": {"name": ["Ada"]}}),
        )

    monkeypatch.setattr(pipeline, "request", native)
    plan, run = tmp_path / "plan.json", tmp_path / "run"
    certification.create_plan(home, ["browserbase-json"], plan)
    certification.run(home, plan, "browserbase-json", run)
    (run / "manifest.json").unlink()
    (run / "certificate.json").unlink()
    (run / "scores.json").unlink()
    after = pipeline.read_json(run / "remote-fixture-after.json")
    for row in after:
        row.pop("http_status", None)
    pipeline.save_json(run / "remote-fixture-after.json", after)
    result = certification.run(home, plan, "browserbase-json", run)
    assert result["cases"] == 1 and len(calls) == 1
    assert pipeline.read_json(run / "remote-fixture-after.json") == after


@pytest.mark.parametrize(
    "value,page,supported",
    [
        ("42", "42.99", False),
        ("2", "-2", False),
        ("234", "1,234", False),
        ("42", "42.", True),
        ("-2", "-2", True),
        ("42.99", "42.99", True),
    ],
)
def test_numeric_references_require_complete_numbers(value, page, supported):
    ref = {"url": "https://example.org/", "values": {"price": [value]}}
    assert (not records.reference_support(ref, ("<p>" + page + "</p>").encode())) is supported


@pytest.mark.parametrize("copies", [1, 2])
@pytest.mark.parametrize("kind", ["text", "url"])
def test_equivalent_reference_values_require_combined_source_occurrences(copies, kind):
    ref = {"url": "https://example.org/", "values": {"item": ["Ada", " Ada "]}}
    raw = b"<p>Ada</p>" * copies
    if kind == "url":
        ref.update(
            values={"item": ["/paper.pdf", "https://example.org/paper.pdf"]},
            value_kinds={"item": "url"},
        )
        raw = b'<a href="/paper.pdf">PDF</a>' * copies
    assert (not records.reference_support(ref, raw)) is (copies == 2)


def test_empty_url_prediction_is_not_the_page_url():
    ref = {
        "url": "https://example.org/",
        "domain": "page",
        "site": "example",
        "values": {"link": ["https://example.org/"]},
        "value_kinds": {"link": "url"},
    }
    for value in ["", " "]:
        scored = records.score({"one": ref}, {"one": {"link": [value]}})
        assert scored["value_micro"]["true_positive"] == 0
        assert (
            analysis.record_field_results(
                ref, {"http_status": 200, "normalized_fields": {"link": [value]}}
            )["link"]["exact"]
            is False
        )
    assert records.score({"one": ref}, {"one": {"link": ["/"]}})["value_micro"]["f1"] == 1


@pytest.mark.parametrize("mode", ["live", "snapshot"])
def test_url_collection_does_not_decode_unused_captured_html(tmp_path, monkeypatch, mode):
    source = definition(tmp_path, mode=mode)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    raw = b'<html><meta charset="iso-8859-1"><h1>Andr\xe9</h1></html>'
    Path(item["input"]).write_bytes(raw)
    item.update(input_sha256=pipeline.digest(raw), values={"name": ["André"]})
    item["qualification"].update(
        input_sha256=pipeline.digest(raw),
        reference_sha256=pipeline.digest(
            json.dumps(item["values"], sort_keys=True, ensure_ascii=False).encode()
        ),
    )
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-bb")
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(status=200, body=raw, url=url),
    )
    sent = []

    def native(url, **kwargs):
        sent.append(kwargs["json_body"])
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps({"statusCode": 200, "content": {"name": ["André"]}}),
        )

    monkeypatch.setattr(pipeline, "request", native)
    plan = tmp_path / "plan.json"
    base = "https://snapshot.example" if mode == "snapshot" else None
    binding = None
    if base:
        version, files = snapshots.inventory(home)
        binding = tmp_path / "snapshots.json"
        pipeline.save_json(
            binding,
            {
                "hosting_provider": "external",
                "datasets": {
                    "sample": {
                        "fixture_base": base,
                        "immutable_origin": base,
                        "html_inventory_sha256": version,
                        "cases": files,
                    }
                },
            },
        )
    certification.create_plan(home, ["browserbase-json"], plan, snapshots=binding)
    monkeypatch.setattr(pipeline, "qualify_fixture", lambda *a, **k: {})
    result = certification.run(home, plan, "browserbase-json", tmp_path / "run")
    assert sent[0]["url"] == (base + "/one" if base else item["url"])
    assert result["scores"]["browserbase-json"]["value_micro"]["f1"] == 1


def test_hf_cached_manifest_can_reference_a_content_addressed_blob(tmp_path, monkeypatch):
    blob = tmp_path / "blob"
    pipeline.save_json(
        blob,
        {
            "files": {
                "html/one.html": {"sha256": "a" * 64},
                "assets/image.png": {"sha256": "b" * 64},
            }
        },
    )
    cached = tmp_path / "manifest.json"
    cached.symlink_to(blob)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda *a, **k: str(cached))
    hf = {
        "repository": "owner/data",
        "input_path": "manifest.json",
        "input_format": "html-manifest",
    }
    assert snapshots.hf_inventory(hf, "c" * 40) == {"one": "a" * 64}
    assert snapshots.hf_resources(hf, "c" * 40) == {"assets/image.png": "b" * 64}


@pytest.mark.parametrize(
    "change",
    [
        None,
        "overlap",
        "missing",
        "page",
        "assets",
        "weaker-policy",
        "stronger-policy",
        "checkpoint-manifest",
        "scorer",
        "settings",
        "budget",
        "pipeline-identity",
        "native-options",
        "scorer-identity",
        "certificate-score",
    ],
)
def test_report_combines_disjoint_certified_scopes_without_repeating_calls(
    tmp_path, monkeypatch, change
):
    import copy

    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-bb")
    sources, definitions, raw_by_url, values_by_url, dispatched = [], [], {}, {}, []
    for cid, name in [("one", "Ada"), ("two", "Grace")]:
        folder = tmp_path / cid
        folder.mkdir()
        source = definition(folder)
        data = pipeline.read_json(source)
        item = data["cases"][0]
        raw = ("<h1>" + name + "</h1>").encode()
        Path(item["input"]).write_bytes(raw)
        item.update(
            id=cid,
            url="https://source.example/" + cid,
            values={"name": [name]},
            input_sha256=pipeline.digest(raw),
        )
        item["qualification"].update(
            input_sha256=pipeline.digest(raw),
            reference_sha256=pipeline.digest(
                json.dumps(item["values"], sort_keys=True, ensure_ascii=False).encode()
            ),
        )
        if change == "weaker-policy":
            from datetime import datetime

            data["live_reference_policy"] = {"maximum_observation_age_seconds": 7200}
            item["qualification"].update(
                source_url=item["url"], observed_at=datetime.now(UTC).isoformat()
            )
        pipeline.save_json(source, data)
        raw_by_url[item["url"]], values_by_url[item["url"]] = raw, item["values"]
        home = folder / "home"
        records.prepare(home, source)
        definitions.append(data)
        sources.append(home)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(status=200, body=raw_by_url[url], url=url),
    )

    def native(url, **kwargs):
        source_url = kwargs["json_body"]["url"]
        dispatched.append(source_url)
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps({"statusCode": 200, "content": values_by_url[source_url]}),
        )

    monkeypatch.setattr(pipeline, "request", native)
    segments = []
    for home in sources:
        plan, run = home / "plan.json", home / "run"
        frozen = certification.create_plan(home, ["browserbase-json"], plan)
        if change == "settings" and home == sources[1]:
            frozen["participants"]["browserbase-json"]["minimum_call_interval_seconds"] = 0.001
            pipeline.save_json(plan, frozen)
        if change == "budget" and home == sources[1]:
            frozen["participants"]["browserbase-json"]["maximum_physical_calls"] = 1
            pipeline.save_json(plan, frozen)
        certification.run(home, plan, "browserbase-json", run)
        segments.append(
            {
                "home": str(home),
                "run": str(run),
                "plan": str(plan),
                "plan_sha256": pipeline.digest(plan.read_bytes()),
            }
        )
    combined = copy.deepcopy(definitions[0])
    combined["cases"] += definitions[1]["cases"]
    original_scores = {
        home / "run/scores.json": (home / "run/scores.json").read_bytes() for home in sources
    }
    if change == "weaker-policy":
        combined.pop("live_reference_policy")
    elif change == "stronger-policy":
        from datetime import datetime

        combined["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
        for item in combined["cases"]:
            item["qualification"].update(
                source_url=item["url"], observed_at=datetime.now(UTC).isoformat()
            )
    elif change == "assets":
        asset = tmp_path / "additional.bin"
        asset.write_bytes(b"different served resources")
        combined["cases"][0]["assets"] = [
            {
                "path": "assets/one/image.bin",
                "input": str(asset),
                "sha256": pipeline.digest(asset.read_bytes()),
            }
        ]
    elif change == "checkpoint-manifest":
        run = sources[0] / "run"
        for file in ["contract.json", "certification-precall.json"]:
            data = pipeline.read_json(run / file)
            data["dataset_manifest_sha256"] = "0" * 64
            pipeline.save_json(run / file, data)
        (run / "certificate.json").unlink()
    elif change == "scorer":
        original_score = records.score

        def different(*args, **kwargs):
            value = original_score(*args, **kwargs)
            value["value_micro"]["f1"] = 0
            return value

        monkeypatch.setattr(records, "score", different)
    if change == "page":
        item = combined["cases"][0]
        path = tmp_path / "another-revision.html"
        path.write_bytes(raw_by_url[item["url"]] + b"<!-- different source bytes -->")
        item.update(input=str(path), input_sha256=pipeline.digest(path.read_bytes()))
        item["qualification"]["input_sha256"] = item["input_sha256"]
    path = tmp_path / "combined.json"
    pipeline.save_json(path, combined)
    home = tmp_path / "combined"
    records.prepare(home, path)
    model = {
        "participants": ["browserbase-json"],
        "dataset_manifest": str(home / "dataset/manifest.json"),
        "evidence_runs": [
            {
                "segments": segments * 2
                if change == "overlap"
                else segments[:1]
                if change == "missing"
                else segments
            }
        ],
    }
    if change in {"pipeline-identity", "native-options", "scorer-identity"}:
        original_source = report.cohort_source

        def changed_configuration(source, *args):
            contract, rows, receipt = original_source(source, *args)
            if source == sources[1] / "run":
                if change == "pipeline-identity":
                    contract = {**contract, "pipeline_sha256": "0" * 64}
                elif change == "native-options":
                    contract = {**contract, "native_options": {"input_route": "html"}}
                else:
                    path = Path(args[0]["plan"])
                    plan = pipeline.read_json(path)
                    plan["scorer_sha256"] = "0" * 64
                    pipeline.save_json(path, plan)
            return contract, rows, receipt

        monkeypatch.setattr(report, "cohort_source", changed_configuration)
    if change == "certificate-score":
        run = sources[1] / "run"
        scores = pipeline.read_json(run / "scores.json")
        scores["scores"]["browserbase-json"]["value_micro"]["f1"] = 0
        pipeline.save_json(run / "scores.json", scores)
        cert = pipeline.read_json(run / "certificate.json")
        cert["scores_sha256"] = pipeline.digest((run / "scores.json").read_bytes())
        pipeline.save_json(run / "certificate.json", cert)
        original_scores.pop(run / "scores.json")
    if change in {None, "stronger-policy", "budget"}:
        result = report.record_cohort_inputs(home, model, profile=home / "profile.json")
        assert set(result["methods"]["browserbase-json"]["results"]) == {"one", "two"}
        assert {row["case_id"] for row in result["native_rows"]} == {"one", "two"}
        assert all("field_results" not in row for row in result["native_rows"])
        assert result["methods"]["browserbase-json"]["statistics"]["certified"]
        assert (
            result["methods"]["browserbase-json"]["statistics"]["scores"]["value_micro"]["f1"] == 1
        )
        if change == "budget":
            from bs4 import BeautifulSoup

            model.update(mode="field-extraction-cohort", title="Segment configuration example",
                         participants={"browserbase-json": {"label": "Browserbase"}},
                         prices={"browserbase-json": {
                             "usd_per_1000": "$4", "terms": {"zh": "Synthetic price"},
                             "sources": ["https://example.org/pricing"], "verified_at": "2026-01-01"
                         }})
            content = home / "report-content.json"
            pipeline.save_json(content, model)
            profile = pipeline.read_json(home / "profile.json")
            profile["research_report"] = {
                "content": {"path": str(content), "sha256": pipeline.digest(content.read_bytes())}
            }
            profile_path = home / "report-profile.json"
            pipeline.save_json(profile_path, profile)
            output = tmp_path / "segment-report"
            report.build(output, content, home=home, profile=profile_path, language="zh")
            report.check(output, profile=profile_path, home=home)
            page = BeautifulSoup(
                (output / "zh/protocol.html").read_text(encoding="utf-8"), "html.parser"
            )
            rows = page.select("[data-segment-configurations] tbody tr")
            assert len(rows) == 2
            assert '"maximum_physical_calls": 1' in rows[1].get_text()
            segments = result["sources"]["browserbase-json"]["segments"]
            for row, segment in zip(rows, segments, strict=True):
                assert segment["contract"]["pipeline_sha256"] in row.get_text()
                assert segment["scorer_sha256"] in row.get_text()
            assert "data-segment-note" in (output / "zh/index.html").read_text(encoding="utf-8")
            cell = rows[0].select("td")[3]
            cell.string = "0" * 64
            protocol = output / "zh/protocol.html"
            protocol.write_text(str(page), encoding="utf-8")
            manifest_path = output / "report-manifest.json"
            manifest = pipeline.read_json(manifest_path)
            manifest["files"]["zh/protocol.html"] = pipeline.digest(protocol.read_bytes())
            pipeline.save_json(manifest_path, manifest)
            with pytest.raises(ValueError, match="Segment configuration"):
                report.check(output, profile=profile_path, home=home)
    else:
        with pytest.raises(
            ValueError, match="segment|changed a task|Full cohort plan|scores do not reproduce"
        ):
            report.record_cohort_inputs(home, model, profile=home / "profile.json")
    assert len(dispatched) == 2
    assert all(path.read_bytes() == raw for path, raw in original_scores.items())


@pytest.mark.parametrize("asset_name", ["image.bin", "frame.html"])
def test_snapshot_resources_are_copied_and_change_the_whole_input_version(
    tmp_path, monkeypatch, asset_name
):
    home = prepared(tmp_path, mode="snapshot", assets=True, asset_name=asset_name)
    version, files = snapshots.inventory(home)
    output = tmp_path / "site"
    binding = snapshots.build(
        {"sample": home}, output, huggingface={"repository": "example/sample", "revision": "1" * 40}
    )
    entry = binding["datasets"]["sample"]
    relative = "assets/one/" + asset_name
    assert (output / entry["path"] / relative).read_bytes() == b"captured-image"
    assert entry["resources"] == snapshots.resource_inventory(home)
    assert "style-src 'self'" in (output / "_headers").read_text(encoding="utf-8")
    response = SimpleNamespace(
        status_code=200,
        url="https://immutable.example/snapshots.json",
        content=(output / "snapshots.json").read_bytes(),
        json=lambda: binding,
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: response)
    snapshots.check_manifest(
        home, "https://immutable.example", {"repository": "example/sample", "revision": "1" * 40}
    )
    resource_path = entry["path"] + "/" + relative
    original_hash = binding["files"][resource_path]
    binding["files"][resource_path] = "wrong"
    with pytest.raises(ValueError, match="(file inventory|resource file binding)"):
        snapshots.check_manifest(
            home,
            "https://immutable.example",
            {"repository": "example/sample", "revision": "1" * 40},
        )
    binding["files"][resource_path] = original_hash
    manifest = pipeline.read_json(home / "dataset/manifest.json")
    asset = home / "dataset" / relative
    asset.write_bytes(b"new captured image")
    manifest["files"][relative] = {
        "sha256": pipeline.digest(asset.read_bytes()),
        "bytes": asset.stat().st_size,
    }
    pipeline.save_json(home / "dataset/manifest.json", manifest)
    profile = pipeline.read_json(home / "profile.json")
    profile["dataset"]["files"][relative] = pipeline.digest(asset.read_bytes())
    profile["dataset"].pop("inventory_sha256")
    pipeline.save_json(home / "profile.json", profile)
    changed, unchanged_html = snapshots.inventory(home)
    assert files == unchanged_html and changed != version


def test_url_fields_share_the_same_equivalence_in_scores_and_details():
    truth = {
        "one": {
            "domain": "article",
            "site": "a",
            "url": "https://example.org/paper/1",
            "value_kinds": {"pdf": "url"},
            "values": {"pdf": ["/pdf/1"], "name": ["PG"]},
        }
    }
    for url, expected in [
        ("https://example.org/pdf/1", True),
        ("https://example.org/pdf/2", False),
    ]:
        predicted = {"pdf": [url], "name": ["PG-13"]}
        score = records.score(truth, {"one": predicted})
        detail = analysis.record_field_results(
            truth["one"],
            {"http_status": 200, "normalized_fields": predicted},
            text_matching="whitespace",
        )
        assert detail["pdf"]["exact"] is expected
        assert detail["name"]["exact"] is False
        assert score["value_micro"]["true_positive"] == int(expected)


def test_source_qualification_rejects_year_substrings_scripts_and_missing_list_items():
    reference = {"url": "https://example.org/list", "values": {"awards": ["2", "2", "3"]}}
    shell = b"<h1>Choose a year</h1><p>2023</p><script>[2,2,3]</script>"
    partial = b"<table><tr><td>2</td></tr><tr><td>3</td></tr></table>"
    complete = b"<table><tr><td>2</td></tr><tr><td>2</td></tr><tr><td>3</td></tr></table>"
    assert records.reference_support(reference, shell)
    assert records.reference_support(reference, partial)
    assert not records.reference_support(reference, complete)


@pytest.mark.parametrize("actual", ["https://[", "http://[", "http://[invalid/path"])
def test_malformed_provider_url_counts_as_wrong_in_scores_and_details(actual):
    truth = {
        "one": {
            "domain": "article",
            "site": "a",
            "url": "https://example.org/",
            "value_kinds": {"pdf": "url"},
            "values": {"pdf": ["https://example.org/paper.pdf"]},
        }
    }
    predicted = {"pdf": [actual]}
    scores = records.score(truth, {"one": predicted})
    detail = analysis.record_field_results(
        truth["one"], {"http_status": 200, "normalized_fields": predicted}
    )
    assert scores["value_micro"]["false_positive"] == 1
    assert scores["value_micro"]["false_negative"] == 1
    assert not detail["pdf"]["exact"]


@pytest.mark.parametrize(
    "counts",
    [
        {"Ada": 1},
        {"Ada": 1, "Grace": 0},
        {"Ada": True, "Grace": 1},
        {"Ada": 1, "Grace": -1},
        {},
        {"Ada": 1, "Grace": 1, "Other": 1},
    ],
)
def test_source_count_overrides_cannot_hide_scored_values(tmp_path, counts):
    source = definition(tmp_path)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    item["values"] = {"name": ["Ada", "Grace"]}
    item["source_value_counts"] = {"name": counts}
    pipeline.save_json(source, data)
    with pytest.raises(ValueError, match="Source value counts"):
        records.prepare(tmp_path / "home", source)
    with pytest.raises(ValueError, match="Source value counts"):
        records.reference_support(item, b"<p>Ada</p>")


@pytest.mark.parametrize("base", ["https://cdn.example.org/", "/media/"])
def test_relative_links_follow_the_first_document_base_in_qualification_and_scoring(tmp_path, base):
    source = definition(tmp_path)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    raw = (
        '<html><head><base href="' + base + '"><base href="https://ignored.example/"></head>'
        '<body><img src="image.png"><a href="http://[">broken unrelated link</a></body></html>'
    ).encode()
    Path(item["input"]).write_bytes(raw)
    item.update(
        input_sha256=pipeline.digest(raw),
        values={"photo": ["image.png"]},
        value_kinds={"photo": "url"},
        schema={
            "type": "object",
            "properties": {"photo": {"type": "array", "items": {"type": "string"}}},
        },
    )
    item["qualification"] = {
        "input_sha256": pipeline.digest(raw),
        "reference_sha256": pipeline.digest(json.dumps(item["values"], sort_keys=True).encode()),
    }
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    ref = pipeline.read_json(home / "dataset/dataset/ground-truth.json")["one"]
    assert records.reference_support(ref, raw) == []
    from urllib.parse import urljoin

    expected = urljoin(urljoin(item["url"], base), "image.png")
    assert records.score({"one": ref}, {"one": {"photo": [expected]}})["value_micro"]["f1"] == 1
    wrong = {
        **ref,
        "values": {"photo": ["https://wrong.example/image.png"]},
        "source_value_counts": {},
    }
    assert records.reference_support(wrong, raw)


@pytest.mark.parametrize("change", ["html", "missing", "declared_html"])
def test_live_plan_cannot_certify_an_html_upload(tmp_path, monkeypatch, change):
    home = prepared(tmp_path)
    plan = tmp_path / "plan.json"
    frozen = certification.create_plan(home, ["cloudflare-json"], plan)
    participant = frozen["participants"]["cloudflare-json"]
    if change == "html":
        participant["native_options"] = {"input_route": "html"}
    elif change == "missing":
        participant.pop("native_options")
    else:
        participant["input"] = "html"
    pipeline.save_json(plan, frozen)
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setattr(
        records,
        "qualify_live",
        lambda *a, **k: pytest.fail("source checked before route validation"),
    )
    with pytest.raises(ValueError, match="[Ll]ive"):
        certification.run(home, plan, "cloudflare-json", tmp_path / "run")
    with pytest.raises(ValueError, match="[Ll]ive"):
        pipeline.probe(
            home,
            ["cloudflare-json"],
            run_dir=tmp_path / "direct",
            native_options={"input_route": "html"},
        )


@pytest.mark.parametrize("instructions", [None, "Discard every other requested record"])
def test_changed_or_missing_frozen_instructions_fail_before_dispatch(
    tmp_path, monkeypatch, instructions
):
    home = prepared(tmp_path)
    plan = tmp_path / "plan.json"
    frozen = certification.create_plan(home, ["browserbase-json"], plan)
    if instructions is None:
        frozen.pop("extraction_instructions")
    else:
        frozen["extraction_instructions"] = instructions
    pipeline.save_json(plan, frozen)
    monkeypatch.setattr(
        records, "qualify_live", lambda *a, **k: pytest.fail("source checked before instructions")
    )
    with pytest.raises(ValueError, match="Frozen extraction instructions"):
        certification.run(home, plan, "browserbase-json", tmp_path / "run")


def test_machine_scored_record_report_accepts_only_its_declared_baseline_policy(
    tmp_path, monkeypatch
):
    from harness.web_extract import example, swde

    baseline = {"text_matching": "whitespace", "unavailable_reference_groups": []}
    monkeypatch.setattr(swde, "reference_policy", lambda *a: baseline, raising=False)
    output = tmp_path / "report"
    example.create_record(output)
    report.check(output)
    path = output / "report-manifest.json"
    manifest = pipeline.read_json(path)
    manifest["reference_policy"] = {"text_matching": "literal", "unavailable_reference_groups": []}
    pipeline.save_json(path, manifest)
    with pytest.raises(ValueError, match="unaudited baseline"):
        report.check(output)


@pytest.mark.parametrize("changed", [False, True])
def test_external_snapshot_checks_resource_bytes_before_and_after_api_calls(
    tmp_path, monkeypatch, changed
):
    home = prepared(tmp_path, mode="snapshot", assets=True)
    state = {"called": False}
    raw = (home / "dataset/html/one.html").read_bytes()
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test-bb")

    def fetch(url, **kwargs):
        if url.startswith("https://api.browserbase.com"):
            state["called"] = True
            return SimpleNamespace(
                status=200,
                headers={},
                text=lambda: json.dumps({"statusCode": 200, "content": {"name": ["Ada"]}}),
            )
        return SimpleNamespace(status=200, headers={}, body=raw, url=url)

    monkeypatch.setattr(pipeline, "request", fetch)
    monkeypatch.setattr(
        "harness.web_extract.snapshots.snapshot_request",
        lambda url, **kwargs: SimpleNamespace(
            status=200,
            body=b"changed" if changed and state["called"] else b"captured-image",
            url=url,
        ),
    )
    run = tmp_path / "run"
    if changed:
        with pytest.raises(ValueError, match="Remote snapshot resource differs"):
            pipeline.probe(
                home,
                ["browserbase-json"],
                full=True,
                fixture_base="https://frozen.example",
                run_dir=run,
            )
        assert list((run / "calls").glob("*.json"))
        assert not (run / "manifest.json").exists()
    else:
        pipeline.probe(
            home,
            ["browserbase-json"],
            full=True,
            fixture_base="https://frozen.example",
            run_dir=run,
        )
        assert (run / "manifest.json").is_file()


def test_managed_manifest_requires_byte_proof_and_external_manifest_checks_resources(
    tmp_path, monkeypatch
):
    home = prepared(tmp_path, mode="snapshot", assets=True)
    version, cases = snapshots.inventory(home)
    receipt = {
        "mode": "dataset_manifest",
        "manifest_url": "https://1234abcd.example.pages.dev/snapshots.json",
        "manifest_sha256": "a" * 64,
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "fixture_base": "https://1234abcd.example.pages.dev/frozen",
    }
    cloudflare = {
        "project": "example",
        "account_id": "account",
        "deployment_id": "1234abcd-0000-0000-0000-000000000000",
    }
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "test-cf"},
    )
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "success": True,
                "result": {
                    "id": cloudflare["deployment_id"],
                    "url": "https://1234abcd.example.pages.dev",
                },
            },
        ),
    )
    monkeypatch.setattr(snapshots, "check_manifest", lambda *a: receipt)
    monkeypatch.setattr(
        pipeline,
        "request",
        lambda url, **kwargs: SimpleNamespace(
            status=200, body=(home / "dataset/html/one.html").read_bytes(), headers={}, url=url
        ),
    )
    monkeypatch.setattr(
        "harness.web_extract.snapshots.snapshot_request",
        lambda url, **kwargs: SimpleNamespace(status=404, body=b"", url=url),
    )
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "cache"))
    with pytest.raises(ValueError, match="Remote snapshot page differs"):
        snapshots.verify_bound_manifest(
            home,
            {**receipt, "hosting_provider": "cloudflare", "cloudflare": cloudflare},
            receipt["fixture_base"],
        )
    with pytest.raises(ValueError, match="Remote snapshot resource differs"):
        snapshots.verify_bound_manifest(
            home, {**receipt, "hosting_provider": "external"}, receipt["fixture_base"]
        )
    with pytest.raises(ValueError, match="Remote snapshot resource differs"):
        snapshots.verify_bound_manifest(
            home, {**receipt, "hosting_provider": "cloudflare"}, receipt["fixture_base"]
        )


@pytest.mark.parametrize(
    "value,supported", [("Ada", True), ("ada", False), ("Ａｄａ", False), ("&amp;", False)]
)
def test_source_text_qualification_uses_the_scoring_text_contract(value, supported):
    reference = {
        "url": "https://example.org/",
        "domain": "person",
        "site": "example",
        "values": {"name": [value]},
    }
    assert (not records.reference_support(reference, b"<h1>Ada</h1>")) is supported
    assert (
        records.score({"one": reference}, {"one": {"name": ["Ada"]}})["value_micro"]["f1"] == 1
    ) is supported


def test_live_reference_check_honors_the_http_charset(tmp_path, monkeypatch):
    source = definition(tmp_path)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    raw = "<html><h1>上海</h1></html>".encode()
    Path(item["input"]).write_bytes(raw)
    item["values"] = {"name": ["上海"]}
    item["input_sha256"] = pipeline.digest(raw)
    item["qualification"] = {
        "input_sha256": pipeline.digest(raw),
        "reference_sha256": pipeline.digest(
            json.dumps(item["values"], sort_keys=True, ensure_ascii=False).encode()
        ),
    }
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(
            status=200, body=raw, url=url, headers={"content-type": "text/html; charset=utf-8"}
        ),
    )
    assert records.qualify_live(home, ["one"])[0]["reference_values_verified"] == 1


def test_managed_snapshot_cannot_reuse_a_different_source_license(tmp_path, monkeypatch):
    home = prepared(tmp_path, mode="snapshot")
    output = tmp_path / "site"
    manifest = snapshots.build(
        {"sample": home}, output, huggingface={"repository": "example/sample", "revision": "1" * 40}
    )
    response = SimpleNamespace(
        status_code=200,
        url="https://immutable.example/snapshots.json",
        content=(output / "snapshots.json").read_bytes(),
        json=lambda: manifest,
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr("requests.get", lambda *a, **k: response)
    identity = {"repository": "example/sample", "revision": "1" * 40}
    snapshots.check_manifest(home, "https://immutable.example", identity)
    prefix = manifest["datasets"]["sample"]["path"]
    manifest["files"][prefix + "/LICENSE.txt"] = "different-license"
    with pytest.raises(ValueError, match="source license"):
        snapshots.check_manifest(home, "https://immutable.example", identity)


@pytest.mark.parametrize(
    "markup,expected,supported",
    [
        ("<span>Micro<b>soft</b></span>", "Microsoft", True),
        ("<div>Micro</div><div>soft</div>", "Microsoft", False),
        ("<div>Micro</div><div>soft</div>", "Micro soft", True),
        ("<p>Ada<br>Lovelace</p>", "Ada Lovelace", True),
    ],
)
def test_visible_source_preserves_inline_words_and_block_boundaries(markup, expected, supported):
    reference = {"url": "https://example.org/", "values": {"name": [expected]}}
    assert (not records.reference_support(reference, markup.encode())) is supported


@pytest.mark.parametrize(
    "markup,value,supported",
    [
        (
            "<span><strong>Developer:</strong> Bloober Team</span>"
            "<span><strong>Platform:</strong></span>",
            "Bloober Team",
            True,
        ),
        ("<button>Ada Lovelace</button><button>Grace Hopper</button>", "Ada Lovelace", True),
        ("<span>42</span>.99", "42", False),
        ("<span>PrefixAdaSuffix</span>", "Ada", False),
    ],
)
def test_source_field_containers_do_not_merge_adjacent_labels(markup, value, supported):
    reference = {"url": "https://example.org/", "values": {"name": [value]}}
    assert (not records.reference_support(reference, markup.encode())) is supported


@pytest.mark.parametrize(
    "markup,values,supported",
    [
        ("<div><!-- Acme --></div>", ["Acme"], False),
        ("<div><!-- hidden -->Acme</div>", ["Acme"], True),
        ("<span>Micro<!-- hidden -->soft</span>", ["Microsoft"], True),
        (
            "<div><button>Acme</button><button>Platform</button><button>Acme</button><button>Platform</button></div>",
            ["Acme", "Acme"],
            True,
        ),
        ("<div><span><span>Acme</span></span><span>Platform</span></div>", ["Acme", "Acme"], False),
        ("<div><span>Acme</span> <span>Acme</span></div>", ["Acme", "Acme", "Acme"], False),
    ],
)
@pytest.mark.parametrize("override", [False, True])
def test_visible_source_counts_distinct_occurrences_without_comments(
    markup, values, supported, override
):
    reference = {"url": "https://example.org/", "values": {"name": values}}
    if override:
        from collections import Counter

        reference["values"]["name"] = list(dict.fromkeys(values))
        reference["source_value_counts"] = {"name": dict(Counter(values))}
    assert (not records.reference_support(reference, markup.encode())) is supported


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_preparation_retries_after_profile_published_but_dataset_not_installed(
    tmp_path, monkeypatch, failure
):
    source = definition(tmp_path)
    home = tmp_path / "home"
    rename = Path.rename
    with monkeypatch.context() as patch:

        def fail_dataset(path, target):
            if Path(target) == home / "dataset":
                raise failure("interrupted dataset installation")
            return rename(path, target)

        patch.setattr(Path, "rename", fail_dataset)
        with pytest.raises(failure):
            records.prepare(home, source)
    assert (home / "profile.json").exists()
    assert not (home / "dataset").exists()
    before = (home / "profile.json").read_bytes()
    records.prepare(home, source)
    assert (home / "profile.json").read_bytes() == before
    pipeline.validate_dataset(home)


def test_preparation_never_overwrites_an_unrelated_profile(tmp_path):
    source = definition(tmp_path)
    home = tmp_path / "home"
    pipeline.save_json(home / "profile.json", {"name": "different cohort"})
    before = (home / "profile.json").read_bytes()
    with pytest.raises(ValueError, match="different cohort"):
        records.prepare(home, source)
    assert (home / "profile.json").read_bytes() == before
    assert not (home / "dataset").exists()


@pytest.mark.parametrize("status", [200, 429])
def test_header_only_charset_is_preserved_for_live_and_captured_sources(
    tmp_path, monkeypatch, status
):
    from datetime import datetime

    source = definition(tmp_path)
    data = pipeline.read_json(source)
    item = data["cases"][0]
    raw = "<html><h1>上海</h1></html>".encode()
    Path(item["input"]).write_bytes(raw)
    item["values"] = {"name": ["上海"]}
    item["input_sha256"] = pipeline.digest(raw)
    item["qualification"].update(
        input_sha256=pipeline.digest(raw),
        reference_sha256=pipeline.digest(
            json.dumps(item["values"], sort_keys=True, ensure_ascii=False).encode()
        ),
        source_url=item["url"],
        observed_at=datetime.now(UTC).isoformat(),
        encoding="utf-8",
    )
    data["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(
            status=status,
            body=raw if status == 200 else b"",
            url=url,
            headers={"content-type": "text/html; charset=utf-8"},
        ),
    )
    receipt = records.qualify_live(home, ["one"])[0]
    assert receipt["encoding"] == "utf-8"
    assert receipt["reference_values_verified"] == 1


@pytest.mark.parametrize(
    "markup,value,supported",
    [
        ("<p>Micro<span>soft</span></p>", "soft", False),
        ("<p><span>Micro</span>soft</p>", "Micro", False),
        ("<p><span>Micro</span><span>soft</span></p>", "Microsoft", True),
        ("<p><span>Micro</span><span>soft</span></p>", "soft", False),
        (
            "<div><span>Acme</span><span>Platform</span><span>Acme</span><span>Platform</span></div>",
            "Acme",
            False,
        ),
        ("<p><span><strong>Kind:</strong>soft</span>ware</p>", "soft", False),
    ],
)
def test_inline_spans_do_not_invent_word_boundaries(markup, value, supported):
    reference = {"url": "https://example.org/", "values": {"name": [value]}}
    assert (not records.reference_support(reference, markup.encode())) is supported


@pytest.mark.parametrize("mode", ["live", "snapshot"])
def test_shared_dispatch_rejects_a_conflicting_dataset_mode_before_calls(
    tmp_path, monkeypatch, mode
):
    home = prepared(tmp_path, mode=mode)
    monkeypatch.setattr(pipeline, "provider_call", lambda *a, **k: pytest.fail("dispatched"))
    with pytest.raises(ValueError, match="input mode differs"):
        pipeline.collect_calls(
            home,
            ["browserbase-json"],
            ["one"],
            tmp_path / "run",
            {"input_mode": "snapshot" if mode == "live" else "live"},
        )


def test_live_cohort_cannot_enter_archived_repair_plan_or_collection(tmp_path):
    from harness.web_extract import repair

    home = prepared(tmp_path)
    for action in [
        lambda: repair.create_plan(home, tmp_path / "absent", tmp_path / "plan", {}, None),
        lambda: repair.collect(home, tmp_path / "absent", "browserbase-json", tmp_path / "run"),
        lambda: repair.run(home, tmp_path / "absent", "browserbase-json", tmp_path / "run"),
        lambda: repair.verify_repair(home, tmp_path / "absent", {}),
    ]:
        with pytest.raises(ValueError, match="resume their original certified run"):
            action()


@pytest.mark.parametrize(
    "current_base", ["https://other.example/page", "https://source.example/media/"]
)
def test_current_redirect_or_base_cannot_rebase_frozen_url_answers(current_base):
    ref = {
        "url": "https://source.example/page",
        "values": {"profile": ["profile"]},
        "value_kinds": {"profile": "url"},
    }
    assert records.reference_support(ref, b'<a href="profile">Person</a>', source_url=current_base)
    assert not records.reference_support(
        ref, b'<a href="https://source.example/profile">Person</a>', source_url=current_base
    )


@pytest.mark.parametrize("policy", [True, False])
@pytest.mark.parametrize("challenge", [True, False])
@pytest.mark.parametrize("positive_reference", [True, False])
def test_http_200_access_gate_is_distinct_from_genuine_reference_drift(
    tmp_path, monkeypatch, policy, challenge, positive_reference
):
    from datetime import datetime

    source = definition(tmp_path)
    data = pipeline.read_json(source)
    if not positive_reference:
        data["cases"][0]["values"] = {"name": []}
        data["cases"][0]["qualification"]["reference_sha256"] = pipeline.digest(
            json.dumps({"name": []}, sort_keys=True, ensure_ascii=False).encode()
        )
    if policy:
        data["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
        data["cases"][0]["qualification"].update(
            source_url=data["cases"][0]["url"], observed_at=datetime.now(UTC).isoformat()
        )
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    body = (
        b'<title>Just a moment...</title><form id="challenge-form"></form>'
        if challenge
        else b"<title>Profile</title><h1>Grace</h1><p>CAPTCHA explained</p>"
    )
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(status=200, body=body, url=url, headers={}),
    )
    if challenge and policy:
        row = records.qualify_live(home, ["one"])[0]
        assert row["source_state"] == "access_unavailable"
        assert row["source_attempts"][0]["access_interstitial"]
    elif not challenge and not positive_reference:
        assert records.qualify_live(home, ["one"])[0]["http_status"] == 200
    else:
        with pytest.raises(
            records.LiveSourceAccessError if challenge else ValueError,
            match="unavailable" if challenge else "no longer supported",
        ):
            records.qualify_live(home, ["one"])


def test_html_upload_uses_the_frozen_capture_charset(tmp_path):
    home = prepared(tmp_path, mode="snapshot")
    path = home / "dataset/provenance.json"
    provenance = pipeline.read_json(path)
    provenance["one"]["qualification"]["encoding"] = "windows-1252"
    pipeline.save_json(path, provenance)
    assert pipeline.captured_html(home, "one", b"Andr\xe9") == "André"


@pytest.mark.parametrize("asset_name", ["image.bin", "page.html", "index.html"])
def test_verified_deployment_reuses_proof_only_for_the_same_inventory_and_files(
    tmp_path, monkeypatch, asset_name
):
    home = prepared(tmp_path, mode="snapshot", assets=True, asset_name=asset_name)
    version, cases = snapshots.inventory(home)
    base = "https://1234abcd.example.pages.dev/frozen"
    cloudflare = {
        "project": "example",
        "account_id": "account",
        "deployment_id": "1234abcd-0000-0000-0000-000000000000",
    }
    binding = {"hosting_provider": "cloudflare", "cloudflare": cloudflare}
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "cache"))
    raw = (home / "dataset/html/one.html").read_bytes()
    state = {"bad": True, "calls": 0}

    def fetch(url, **kw):
        state["calls"] += 1
        body = raw if url.endswith("/one") else b"wrong" if state["bad"] else b"captured-image"
        return SimpleNamespace(status=200, body=body, url=url.removesuffix(".html"))

    monkeypatch.setattr("harness.web_extract.snapshots.snapshot_request", fetch)
    with pytest.raises(ValueError, match="resource differs"):
        snapshots.deployment_proof(home, binding, base)
    assert not list((tmp_path / "cache").rglob("*.json"))
    state["bad"] = False
    original_save = snapshots.save_json

    def another_first_user(path, value, **options):
        # A second evaluator finishes the same initial audit just before this one.
        original_save(path, {**value, "verified_at": "2026-11-01T00:00:00+00:00"}, exclusive=True)
        return original_save(path, value, **options)

    with monkeypatch.context() as concurrent:
        concurrent.setattr(snapshots, "save_json", another_first_user)
        first = snapshots.deployment_proof(home, binding, base)
    assert first["verified_at"] == "2026-11-01T00:00:00+00:00"
    count = state["calls"]
    assert snapshots.deployment_proof(home, binding, base) == first
    assert state["calls"] == count
    assert first["html_inventory_sha256"] == version
    assert first["page_count"] == 1 and first["resource_count"] == 1
    # A different immutable deployment cannot inherit this proof.
    changed = {
        **binding,
        "cloudflare": {**cloudflare, "deployment_id": "abcdef12-0000-0000-0000-000000000000"},
    }
    snapshots.deployment_proof(home, changed, base.replace("1234abcd", "abcdef12"))
    assert state["calls"] > count
    receipt = {
        "hosting_provider": "cloudflare",
        "mode": "dataset_manifest",
        "fixture_base": base,
        "html_inventory_sha256": version,
        "case_count": 1,
        "manifest_sha256": "a" * 64,
        "cloudflare": cloudflare,
    }
    rows = snapshots.manifest_receipt(home, receipt, base) + [first]
    snapshots.validate_fixture_receipts(home, rows, base, ["one"], bound=receipt)
    for field, value in [
        ("resource_count", 0),
        ("html_inventory_sha256", "b" * 64),
        ("fixture_base", "https://wrong.example"),
    ]:
        wrong = [rows[0], {**first, field: value}]
        with pytest.raises(ValueError, match="byte proof differs"):
            snapshots.validate_fixture_receipts(home, wrong, base, ["one"], bound=receipt)


def test_static_snapshot_requests_reuse_connections_and_enforce_byte_limits(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    clients = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            clients.append(self.client_address)
            body = b"page" if self.path == "/page" else b"too-large"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    state = SimpleNamespace()
    monkeypatch.setattr(snapshots, "_SNAPSHOT_HTTP", state)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for _ in range(2):
            assert (
                snapshots.snapshot_request(base + "/page", headers={}, timeout=2, max_bytes=4).body
                == b"page"
            )
        assert clients[0] == clients[1]
        with pytest.raises(ValueError, match="exceeds its frozen byte size"):
            snapshots.snapshot_request(base + "/large", headers={}, timeout=2, max_bytes=4)
    finally:
        if hasattr(state, "session"):
            state.session.close()
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_concurrent_snapshot_users_share_one_complete_initial_byte_audit(tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    home = prepared(tmp_path, mode="snapshot", assets=True)
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "cache"))
    base = "https://1234abcd.example.pages.dev/frozen"
    binding = {
        "cloudflare": {
            "project": "example",
            "account_id": "account",
            "deployment_id": "1234abcd-0000-0000-0000-000000000000",
        }
    }
    observed = []
    start = threading.Barrier(2)

    def fetch(url, **options):
        observed.append(url)
        time.sleep(0.02)
        body = (
            (home / "dataset/html/one.html").read_bytes()
            if url.endswith("/one")
            else b"captured-image"
        )
        return SimpleNamespace(status=200, body=body, url=url)

    monkeypatch.setattr(snapshots, "snapshot_request", fetch)

    def user(_):
        start.wait(timeout=3)
        return snapshots.deployment_proof(home, binding, base)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(user, range(2))
    assert first == second
    assert len(observed) == 2  # One HTML page and one resource, not two full audits.


def test_invalid_resource_stops_queued_snapshot_downloads(tmp_path, monkeypatch):
    import time

    home = prepared(tmp_path, mode="snapshot", assets=True)
    path = home / "dataset/manifest.json"
    manifest = pipeline.read_json(path)
    for index in range(20):
        relative = f"assets/other/{index:02}.bin"
        asset = home / "dataset" / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"captured-image")
        manifest["files"][relative] = {"sha256": pipeline.digest(asset.read_bytes())}
    pipeline.save_json(path, manifest)
    called = []

    def fetch(url, **options):
        called.append(url)
        if url.endswith("/image.bin"):
            return SimpleNamespace(status=404, body=b"", url=url)
        time.sleep(0.02)
        return SimpleNamespace(status=200, body=b"captured-image", url=url)

    monkeypatch.setattr(snapshots, "snapshot_request", fetch)
    with pytest.raises(ValueError, match="resource differs"):
        snapshots.verify_resources(home, "https://frozen.example", workers=1)
    assert len(called) < 21


@pytest.mark.parametrize("encoding", [
    "windows-1252", "utf-8", "undeclared-latin1", "undeclared-utf8",
])
def test_qualified_html_reaches_the_upload_provider(tmp_path, monkeypatch, encoding):
    source = definition(tmp_path, mode="snapshot")
    data = pipeline.read_json(source)
    item = data["cases"][0]
    raw = (
        b"<html><h1>Andr\xe9</h1></html>" if encoding == "undeclared-latin1"
        else "<html><h1>André</h1></html>".encode() if encoding == "undeclared-utf8"
        else ('<html><meta charset="' + encoding + '"><h1>André</h1></html>').encode(encoding)
    )
    assert records.reference_support({"url": item["url"], "values": {"name": ["André"]}}, raw) == []
    if encoding == "utf-8":
        raw += b"<footer>Damaged unrelated text: \xff</footer>"
    Path(item["input"]).write_bytes(raw)
    item.update(input_sha256=pipeline.digest(raw), values={"name": ["André"]})
    item["qualification"].update(
        input_sha256=pipeline.digest(raw),
        reference_sha256=pipeline.digest(
            json.dumps(item["values"], sort_keys=True, ensure_ascii=False).encode()
        ),
    )
    assert "encoding" not in item["qualification"]
    pipeline.save_json(source, data)
    home = tmp_path / "home"
    records.prepare(home, source)
    frozen = pipeline.read_json(home / "dataset/provenance.json")["one"]["qualification"]
    assert frozen["encoding"]
    assert pipeline.captured_html(home, "one", raw).find("André") >= 0
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "synthetic")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic")
    sent = []

    def provider(method, html, *args, **options):
        sent.append(html)
        return {"http_status": 200, "response": {"result": {"name": ["André"]}}}

    monkeypatch.setattr(pipeline, "provider_call", provider)
    run = pipeline.probe(home, ["cloudflare-json"], full=True, run_dir=tmp_path / "run")
    rows = pipeline.load_run(run)[1]
    assert len(sent) == 1 and "André" in sent[0]
    assert rows[0]["compatibility"] == "structured_output"
    assert len((run / "calls.journal.jsonl").read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize("hosting", ["external", "cloudflare"])
@pytest.mark.parametrize(
    "identity", [None, {}, {"project": "example", "account_id": "account", "deployment_id": "bad"}]
)
def test_unqualified_hosts_cannot_substitute_a_deployment_proof_for_byte_receipts(
    tmp_path, hosting, identity
):
    home = prepared(tmp_path, mode="snapshot", assets=True)
    version, cases = snapshots.inventory(home)
    base = "https://1234abcd.example.pages.dev/frozen"
    bound = {
        "hosting_provider": hosting,
        "mode": "dataset_manifest",
        "fixture_base": base,
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "manifest_sha256": "a" * 64,
    }
    proof = {
        "scope": "deployment",
        "case_id": "deployment-bytes",
        "schema": "web-extract-deployment-bytes/1",
        "fixture_base": base,
        "html_inventory_sha256": version,
        "page_count": len(cases),
        "resource_count": 1,
        "verified_at": "2026-11-01T00:00:00+00:00",
    }
    if identity is not None:
        bound["cloudflare"] = identity
        proof["cloudflare"] = identity
    rows = snapshots.manifest_receipt(home, bound, base) + [proof]
    with pytest.raises(ValueError, match="deployment byte proof differs"):
        snapshots.validate_fixture_receipts(home, rows, base, ["one"], bound=bound)


def test_managed_proof_cannot_qualify_a_different_url_suffix(tmp_path, monkeypatch):
    home = prepared(tmp_path, mode="snapshot", assets=True)
    version, cases = snapshots.inventory(home)
    base = "https://1234abcd.example.pages.dev/frozen"
    identity = {
        "project": "example",
        "account_id": "account",
        "deployment_id": "1234abcd-0000-0000-0000-000000000000",
    }
    receipt = {
        "mode": "dataset_manifest",
        "manifest_url": "https://1234abcd.example.pages.dev/snapshots.json",
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "manifest_sha256": "a" * 64,
        "fixture_base": base,
        "html_inventory_sha256": version,
        "case_count": len(cases),
    }
    bound = {**receipt, "hosting_provider": "cloudflare", "cloudflare": identity}
    monkeypatch.setattr(snapshots, "check_manifest", lambda *a: receipt)
    monkeypatch.setattr(snapshots, "managed_cloudflare", lambda *a: True)
    monkeypatch.setattr(
        snapshots, "deployment_proof", lambda *a: pytest.fail("wrong URL route was audited")
    )
    with pytest.raises(ValueError, match="empty fixture suffix"):
        snapshots.verify_bound_manifest(home, bound, base, suffix=".html")
    proof = {
        "scope": "deployment",
        "case_id": "deployment-bytes",
        "schema": "web-extract-deployment-bytes/1",
        "fixture_base": base,
        "cloudflare": identity,
        "html_inventory_sha256": version,
        "page_count": len(cases),
        "resource_count": 1,
        "verified_at": "2026-11-01T00:00:00+00:00",
    }
    rows = snapshots.manifest_receipt(home, bound, base) + [proof]
    snapshots.validate_fixture_receipts(home, rows, base, ["one"], bound=bound)
    with pytest.raises(ValueError, match="deployment byte proof differs"):
        snapshots.validate_fixture_receipts(home, rows, base, ["one"], suffix=".html", bound=bound)


@pytest.mark.parametrize("confirmation", [401, 403, "timeout", "mismatch"])
def test_unavailable_cloudflare_confirmation_uses_bytes_but_mismatch_stops(
    tmp_path, monkeypatch, confirmation
):
    import requests

    home = prepared(tmp_path, mode="snapshot", assets=True)
    version, cases = snapshots.inventory(home)
    origin = "https://1234abcd.example.pages.dev"
    base = origin + "/frozen"
    identity = {
        "project": "example",
        "account_id": "account",
        "deployment_id": "1234abcd-0000-0000-0000-000000000000",
    }
    receipt = {
        "mode": "dataset_manifest",
        "manifest_url": origin + "/snapshots.json",
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "manifest_sha256": "a" * 64,
        "fixture_base": base,
        "html_inventory_sha256": version,
        "case_count": len(cases),
    }
    bound = {**receipt, "hosting_provider": "cloudflare", "cloudflare": identity}
    monkeypatch.setattr(snapshots, "check_manifest", lambda *a: receipt)
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_API_TOKEN": "expired-or-unavailable",
        },
    )

    def confirm(*args, **options):
        if confirmation == "timeout":
            raise requests.Timeout("Control API unavailable")
        response = requests.Response()
        response.url = args[0]
        response.status_code = confirmation if isinstance(confirmation, int) else 200
        response._content = json.dumps(
            {"success": True, "result": {"id": "different-deployment", "url": origin}}
        ).encode()
        return response

    monkeypatch.setattr("requests.get", confirm)
    observed = []

    def page(url, **options):
        observed.append("page")
        return SimpleNamespace(
            status=200, body=(home / "dataset/html/one.html").read_bytes(), headers={}, url=url
        )

    def resource(url, **options):
        observed.append("resource")
        return SimpleNamespace(status=200, body=b"captured-image", url=url)

    monkeypatch.setattr(pipeline, "request", page)
    monkeypatch.setattr(snapshots, "snapshot_request", resource)
    if confirmation == "mismatch":
        with pytest.raises(ValueError, match="identity differs"):
            snapshots.verify_bound_manifest(home, bound, base)
        assert observed == []
        return
    rows = snapshots.verify_bound_manifest(home, bound, base)
    assert observed == ["page", "resource"]
    snapshots.validate_fixture_receipts(home, rows, base, ["one"], bound=bound)
    binding = {
        "hosting_provider": "cloudflare",
        "cloudflare": identity,
        "snapshot_manifest": receipt,
        "datasets": {
            "sample": {
                "fixture_base": base,
                "immutable_origin": origin,
                "html_inventory_sha256": version,
                "cases": cases,
            }
        },
    }
    published = snapshots.publication_metadata(
        home, binding, {"pages": rows[1:2], "resources": rows[2:]}
    )
    assert published["snapshot_manifest"] == receipt


@pytest.mark.parametrize("gate", [
    b'<title>Just a moment...</title><form id="challenge-form"></form>',
    b'<title>Sign in</title><form><input type="password"></form>',
])
def test_empty_reference_cannot_qualify_an_access_gate_as_captured_fallback(
    tmp_path, monkeypatch, gate
):
    from datetime import datetime

    source = definition(tmp_path)
    model = pipeline.read_json(source)
    item = model["cases"][0]
    Path(item["input"]).write_bytes(gate)
    item.update(input_sha256=pipeline.digest(gate), values={"name": []})
    item["qualification"].update(
        input_sha256=pipeline.digest(gate), source_url=item["url"],
        observed_at=datetime.now(UTC).isoformat(),
        reference_sha256=pipeline.digest(
            json.dumps(item["values"], sort_keys=True, ensure_ascii=False).encode()
        ),
    )
    model["live_reference_policy"] = {"maximum_observation_age_seconds": 3600}
    pipeline.save_json(source, model)
    home = tmp_path / "home"
    records.prepare(home, source)
    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kw: SimpleNamespace(status=200, body=gate, url=url, headers={}),
    )
    with pytest.raises(records.LiveSourceAccessError, match="unavailable"):
        records.qualify_live(home, ["one"])


def test_undeclared_utf8_is_frozen_even_when_references_are_ascii(tmp_path):
    source = definition(tmp_path, mode="snapshot")
    model = pipeline.read_json(source)
    item = model["cases"][0]
    raw = '<h1>Ada</h1><p>价格 ¥24</p>'.encode()
    Path(item["input"]).write_bytes(raw)
    item["input_sha256"] = item["qualification"]["input_sha256"] = pipeline.digest(raw)
    pipeline.save_json(source, model)
    home = tmp_path / "home"
    records.prepare(home, source)
    assert records.reference_support({"url": item["url"], "values": item["values"]}, raw) == []
    receipt = pipeline.read_json(home / "dataset/provenance.json")["one"]["qualification"]
    assert receipt["encoding"] == "utf-8"
    assert pipeline.captured_html(home, "one", raw) == raw.decode()
