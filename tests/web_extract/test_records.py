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


def test_live_url_collection_does_not_decode_unused_captured_html(tmp_path, monkeypatch):
    source = definition(tmp_path)
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
    certification.create_plan(home, ["browserbase-json"], plan)
    result = certification.run(home, plan, "browserbase-json", tmp_path / "run")
    assert sent[0]["url"] == item["url"]
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
        certification.create_plan(home, ["browserbase-json"], plan)
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
    if change in {None, "stronger-policy"}:
        result = report.record_cohort_inputs(home, model, profile=home / "profile.json")
        assert set(result["methods"]["browserbase-json"]["results"]) == {"one", "two"}
        assert result["methods"]["browserbase-json"]["statistics"]["certified"]
        assert (
            result["methods"]["browserbase-json"]["statistics"]["scores"]["value_micro"]["f1"] == 1
        )
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
        "harness.web_fetch.providers.http_client.request",
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


def test_managed_manifest_skips_resource_fetch_but_external_manifest_does_not(
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
        "harness.web_fetch.providers.http_client.request",
        lambda url, **kwargs: SimpleNamespace(status=404, body=b"", url=url),
    )
    assert (
        len(
            snapshots.verify_bound_manifest(
                home,
                {**receipt, "hosting_provider": "cloudflare", "cloudflare": cloudflare},
                receipt["fixture_base"],
            )
        )
        == 1
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
            "<span><strong>Developer:</strong> Bloober Team</span><span>Platform:</span>",
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
            "<div><span>Acme</span><span>Platform</span><span>Acme</span><span>Platform</span></div>",
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
