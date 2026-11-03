"""Failure-only correction and complete source-review behavior contracts."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_scraper.example import create
from harness.web_scraper.pipeline import digest, read_json, save_json
from harness.web_scraper.repair import compose, create_plan, run
from harness.web_scraper.review import prepare, reconcile_citations, validate


def review_payload(packet, quote="synthetic product"):
    finding = {
        "status": "supported",
        "values": [],
        "source_quote": quote,
        "source_locator": "p",
        "reason": "Synthetic source context",
    }
    fields = {field: deepcopy(finding) for field in ("price", "sku", "availability")}
    return {
        "cases": [
            {
                "case_id": case["case_id"],
                "main_product": "Synthetic",
                "source_fields": fields,
                "reference": fields,
                "outputs": [
                    {"method": output["method"], "fields": fields, "projection_issue": ""}
                    for output in case.get("outputs", [])
                ],
            }
            for case in packet["cases"]
        ]
    }


@pytest.fixture(autouse=True)
def native_test_credentials(monkeypatch):
    from harness.web_scraper.pipeline import available_methods

    for method in available_methods().values():
        for key in method["keys"]:
            monkeypatch.setenv(key, "synthetic-test-credential")


def parent_inputs(tmp_path, method="cloudflare-json", failed_overrides=None, success_count=2):
    home = create(tmp_path / "example")
    source = home / "runs/parent"
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    cases = sorted(truth)
    contract = {"methods": [method], "case_ids": cases}
    save_json(source / "contract.json", contract)
    entries, rows = [], []
    for i, cid in enumerate(cases):
        row = {
            "method": method,
            "case_id": cid,
            "input_sha256": digest((home / "dataset/html" / (cid + ".html")).read_bytes()),
            "http_status": 200 if i < success_count else None,
            "compatibility": "structured_output" if i < success_count else "no_structured_output",
            "normalized_fields": {"sku": str(i)} if i < success_count else None,
            "response": {"result": {"sku": str(i)}} if i < success_count else {},
        }
        if i >= success_count:
            row.update(failed_overrides or {})
        path = source / "calls" / (cid + ".json")
        save_json(path, row)
        entries.append({"path": "calls/" + path.name, "sha256": digest(path.read_bytes())})
        rows.append(row)
    save_json(
        source / "manifest.json",
        {
            "contract_sha256": digest((source / "contract.json").read_bytes()),
            "calls": entries,
        },
    )
    save_json(
        source / "certificate.json",
        {
            "manifest_sha256": digest((source / "manifest.json").read_bytes()),
        },
    )
    content = tmp_path / "content.json"
    save_json(content, {"evidence_runs": [{"run": str(source)}]})
    profile = tmp_path / "repair-plan.json"
    create_plan(home, content, profile, {}, "https://frozen.example")
    return home, source, profile, rows


def test_repair_calls_only_original_failures_and_resumes_without_new_calls(tmp_path):
    home, source, profile, rows = parent_inputs(tmp_path)
    original = {p: p.read_bytes() for p in source.rglob("*.json")}
    destination = tmp_path / "repaired"
    response = {
        "state": "response_received",
        "http_status": 200,
        "response": {"result": {"sku": "new", "price": 5}},
    }
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch("harness.web_scraper.repair.provider_call", return_value=response) as provider,
    ):
        run(home, profile, "cloudflare-json", destination)
        run(home, profile, "cloudflare-json", destination)
    assert provider.call_count == 1
    assert {p: p.read_bytes() for p in original} == original
    revision = {
        "path": str(destination),
        "sha256": digest((destination / "certificate.json").read_bytes()),
    }
    with patch("harness.web_scraper.repair.score_rows", return_value={}):
        current, _ = compose(home, source, deepcopy(rows), revision)
    assert current[:2] == rows[:2]
    assert current[2]["normalized_fields"]["sku"] == "new"
    assert current[2]["elapsed_seconds"] >= 0
    assert current[2]["retry_missing_timers"] == 1
    assert len(current[2]["physical_attempts"]) == 2
    certificate = read_json(destination / "certificate.json")
    certificate["profile_sha256"] = "f" * 64
    save_json(destination / "certificate.json", certificate)
    revision["sha256"] = digest((destination / "certificate.json").read_bytes())
    with pytest.raises(ValueError, match="identities differ"):
        compose(home, source, deepcopy(rows), revision)


def test_repair_cannot_replace_original_successes(tmp_path):
    home, _, profile, rows = parent_inputs(tmp_path)
    plan = read_json(profile)
    plan["participants"]["cloudflare-json"]["case_ids"].append(rows[0]["case_id"])
    save_json(profile, plan)
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.provider_call") as provider,
    ):
        with pytest.raises(ValueError, match="never successes"):
            run(home, profile, "cloudflare-json", tmp_path / "wrong")
    provider.assert_not_called()


def test_ambiguous_repair_dispatch_is_not_repeated(tmp_path):
    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "interrupted"
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch(
            "harness.web_scraper.repair.provider_call", side_effect=KeyboardInterrupt
        ) as provider,
    ):
        with pytest.raises(KeyboardInterrupt):
            run(home, profile, "cloudflare-json", target)
        with pytest.raises(ValueError, match="Ambiguous repair dispatch"):
            run(home, profile, "cloudflare-json", target)
    assert provider.call_count == 1


def test_client_error_gets_a_timer_and_remains_a_failed_result(tmp_path):
    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "timeout"
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.provider_call", side_effect=TimeoutError("network")),
    ):
        run(home, profile, "cloudflare-json", target)
    row = read_json(next((target / "calls").glob("*.json")))
    assert row["compatibility"] == "no_structured_output"
    assert row["elapsed_seconds"] >= 0
    assert row["error"] == "network"


def test_citation_reconciliation_changes_whitespace_only_and_keeps_raw_opinion(tmp_path):
    source = tmp_path / "source.html"
    outline = tmp_path / "source.txt"
    source.write_text("<p>Price: 13,40\u00a0€</p>", encoding="utf-8")
    outline.write_text("Price: 13,40\u00a0€", encoding="utf-8")
    packet = {
        "cases": [
            {
                "case_id": "one",
                "source_html": str(source),
                "source_outline": str(outline),
                "outputs": [],
            }
        ]
    }
    raw = review_payload(packet, "13,40 €")
    corrected = reconcile_citations(raw, packet)
    assert corrected["cases"][0]["source_fields"]["price"]["source_quote"] == "13,40\u00a0€"
    assert raw["cases"][0]["source_fields"]["price"]["source_quote"] == "13,40 €"
    raw = review_payload(packet, "13,41 €")
    unchanged = reconcile_citations(raw, packet)
    assert unchanged["cases"][0]["source_fields"]["price"]["source_quote"] == "13,41 €"
    assert "citation_format_corrections" not in unchanged


def test_field_diagnostics_preserve_missing_false_positive_and_decimal_equivalence(tmp_path):
    from harness.web_scraper.analysis import field_results

    home = create(tmp_path / "example")
    expected = {"price": ["13.40"], "sku": ["0007"], "availability": ["InStock"]}
    row = {"http_status": 200, "normalized_fields": {"price": "13.4", "sku": "7"}}
    result = field_results(home, expected, row, profile=home / "profile.json")
    assert result["price"]["status"] == "correct"
    assert (result["sku"]["tp"], result["sku"]["fp"], result["sku"]["fn"]) == (0, 1, 1)
    assert result["availability"]["status"] == "missing"
    result = field_results(home, {}, row, profile=home / "profile.json")
    assert result["price"]["status"] == "unexpected"


def test_baseline_stock_indicator_projection_preserves_unknown_and_explicit_values():
    from harness.web_scraper.pipeline import check_projection_controls, normalize

    check_projection_controls()
    assert normalize("extruct", {"result": {"InStock": "InStock"}})["availability"] == "InStock"
    ambiguous = {"InStock": "InStock", "OutOfStock": "OutOfStock"}
    assert "availability" not in normalize("extruct", {"result": ambiguous})
    assert "availability" not in normalize("extruct", {"result": {"InStock": True}})


def test_complete_audit_rejects_unreviewed_and_substituted_native_answers(tmp_path):
    from harness.web_scraper.review import assemble

    home = create(tmp_path / "example")
    work = tmp_path / "initial-review"
    manifest = prepare(home, home / "content.json", work)
    finding = {
        "status": "supported",
        "values": [],
        "source_quote": "synthetic product",
        "source_locator": "p",
        "reason": "Synthetic source checked",
    }
    for entry in manifest["packets"]:
        packet = read_json(Path(entry["path"]))
        result = {"cases": []}
        for case in packet["cases"]:
            fields = {f: deepcopy(finding) for f in ("price", "sku", "availability")}
            result["cases"].append(
                {
                    "case_id": case["case_id"],
                    "main_product": "Synthetic",
                    "source_fields": fields,
                    "reference": fields,
                    "outputs": [
                        {"method": o["method"], "fields": fields, "projection_issue": "None"}
                        for o in case["outputs"]
                    ],
                }
            )
        save_json(work / "reviews" / Path(entry["path"]).name, result)
    save_json(work / "reviewer.json", {"model": "synthetic-test"})
    for name in ("supplemental", "adjudication"):
        save_json(
            tmp_path / name / "manifest.json",
            {"bindings": {}, "packets": [], "case_count": 0, "result_count": 0},
        )
    arguments = (
        home,
        home / "content.json",
        work,
        tmp_path / "supplemental",
        tmp_path / "adjudication",
    )
    result = assemble(*arguments, tmp_path / "complete.json", profile=home / "profile.json")
    assert result["result_count"] == 6
    assert result["accepted_unreviewed_passes"] == result["unresolved"] == 0
    # Preserve adverse reviewer judgments and reject a later failed execution with
    # an identical response body but different runtime evidence.
    from harness.web_scraper.pipeline import load_run
    from harness.web_scraper.report import build

    audit_path = tmp_path / "complete.json"
    content_path = home / "content.json"
    content = read_json(content_path)
    content["audit"] = {"path": str(audit_path), "sha256": digest(audit_path.read_bytes())}
    save_json(content_path, content)
    build(tmp_path / "reviewed-report", content_path, home=home, profile=home / "profile.json")
    finding = next(iter(result["results"].values()))
    assert finding["reviewer_fields"]["sku"]["status"] == "supported"
    assert finding["field_reconciliation"]["sku"]["resolution"]
    source_run = home / "runs/example-v1"
    _, original_rows = load_run(source_run)
    substituted = deepcopy(original_rows)
    failed = next(row for row in substituted if row["method"] == "synthetic-blocked")
    failed["error"] = "A different runtime failure with the same empty response"
    with patch(
        "harness.web_scraper.report.load_run",
        return_value=(read_json(source_run / "contract.json"), substituted),
    ):
        with pytest.raises(ValueError, match="different current execution"):
            build(
                tmp_path / "wrong-execution", content_path, home=home, profile=home / "profile.json"
            )
    content.pop("audit")
    save_json(content_path, content)
    # Even an unchanged native body cannot transfer review to a different run.
    original_run = home / "runs/example-v1"
    alternate = home / "runs/alternate"
    import shutil

    shutil.copytree(original_run, alternate)
    altered_manifest = read_json(alternate / "manifest.json")
    failed_entry = next(
        e
        for e in altered_manifest["calls"]
        if read_json(alternate / e["path"])["method"] == "synthetic-blocked"
    )
    failed_row = read_json(alternate / failed_entry["path"])
    failed_row["error"] = "Different unreviewed transport error"
    save_json(alternate / failed_entry["path"], failed_row)
    failed_entry["sha256"] = digest((alternate / failed_entry["path"]).read_bytes())
    save_json(alternate / "manifest.json", altered_manifest)
    old_content = read_json(content_path)
    changed_content = deepcopy(old_content)
    changed_content["evidence_runs"][0]["run"] = "alternate"
    save_json(content_path, changed_content)
    with pytest.raises(ValueError, match="Original audit call identity differs"):
        assemble(*arguments, tmp_path / "unreviewed-execution.json", profile=home / "profile.json")
    save_json(content_path, old_content)
    opinion_path = work / "reviews/000.json"
    prior_opinion = opinion_path.read_bytes()
    opinions = read_json(opinion_path)
    opinions["cases"][0]["outputs"][0]["fields"]["sku"]["status"] = "incorrect"
    opinions["cases"][0]["outputs"][0]["projection_issue"] = "A retained projection concern"
    save_json(opinion_path, opinions)
    adverse = assemble(*arguments, tmp_path / "adverse.json", profile=home / "profile.json")
    selected = adverse["results"][
        opinions["cases"][0]["outputs"][0]["method"] + ":" + opinions["cases"][0]["case_id"]
    ]
    assert selected["reviewer_fields"]["sku"]["status"] == "incorrect"
    assert selected["reviewer_projection_issue"] == "A retained projection concern"
    opinion_path.write_bytes(prior_opinion)

    cid = "1" * 64
    decision = tmp_path / "retained-reference.json"
    source = home / "dataset/html" / (cid + ".html")
    save_json(
        decision,
        {
            "decisions": [
                {
                    "case_id": cid,
                    "field": "price",
                    "accepted": [],
                    "reason": "No price is present in this synthetic source",
                    "source_sha256": digest(source.read_bytes()),
                    "source_quote": "synthetic product",
                    "source_locator": "main paragraph",
                }
            ]
        },
    )
    retained = assemble(
        *arguments, tmp_path / "retained.json", profile=home / "profile.json", decisions=decision
    )
    assert retained["sources"][cid]["source_fields"]["price"]["status"] == "absent"
    assert retained["sources"][cid]["reference_review"]["price"]["status"] == "supported"
    assert retained["reference_changes"] == []
    changed = read_json(decision)
    changed["decisions"][0].update(field="sku", accepted=["synthetic product"])
    save_json(decision, changed)
    amended = assemble(
        *arguments, tmp_path / "amended.json", profile=home / "profile.json", decisions=decision
    )
    assert amended["audited_reference"][cid]["sku"] == ["synthetic product"]
    assert amended["reference_changes"][0]["accepted"] == ["synthetic product"]
    assert amended["sources"][cid]["reference_review"]["sku"]["status"] == "incorrect"
    # A supported adjudication must retain the original reference, even if its
    # separately proposed source values differ. Explicit final decisions are tested above.
    from harness.web_scraper.review import prepare_adjudication

    review_path = work / "reviews" / "000.json"
    prior_bytes = review_path.read_bytes()
    prior = read_json(review_path)
    prior["cases"][0]["reference"]["sku"]["status"] = "incorrect"
    save_json(review_path, prior)
    rules = tmp_path / "rules.txt"
    rules.write_text("Retain supported original annotations", encoding="utf-8")
    adjudication_work = tmp_path / "supported-adjudication"
    original_source = source.read_bytes()
    source.write_bytes(original_source + b"<!-- outside the retained quotation -->")
    with pytest.raises(ValueError, match="Adjudication source changed"):
        prepare_adjudication(work, adjudication_work, rules)
    source.write_bytes(original_source)
    adjudication_manifest = prepare_adjudication(work, adjudication_work, rules)
    for entry in adjudication_manifest["packets"]:
        packet = read_json(Path(entry["path"]))
        opinions = []
        for case in packet["cases"]:
            opinion = deepcopy(prior["cases"][0])
            opinion["case_id"] = case["case_id"]
            opinion["outputs"] = []
            opinion["reference"]["sku"]["status"] = "supported"
            opinion["source_fields"]["sku"]["values"] = ["synthetic product"]
            opinions.append(opinion)
        save_json(adjudication_work / "reviews" / Path(entry["path"]).name, {"cases": opinions})
    from harness.web_scraper.review import completed

    source.write_bytes(original_source + b"<!-- changed after preparation -->")
    with pytest.raises(ValueError, match="Audit source changed"):
        completed(adjudication_work)
    source.write_bytes(original_source)
    supported = assemble(
        home,
        home / "content.json",
        work,
        tmp_path / "supplemental",
        adjudication_work,
        tmp_path / "supported.json",
        profile=home / "profile.json",
    )
    assert supported["audited_reference"][cid]["sku"] == ["1"]
    assert supported["reference_changes"] == []
    review_path.write_bytes(prior_bytes)
    path = Path(manifest["packets"][0]["path"])
    packet = read_json(path)
    packet["cases"][0]["outputs"][0]["native_response"] = {"substituted": True}
    save_json(path, packet)
    manifest["packets"][0]["sha256"] = digest(path.read_bytes())
    save_json(work / "manifest.json", manifest)
    with pytest.raises(ValueError, match="Reviewed native response differs"):
        assemble(*arguments, tmp_path / "wrong.json", profile=home / "profile.json")


def test_review_freezes_complete_outputs_and_rejects_missing_field_or_method(tmp_path):
    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work)
    assert manifest["result_count"] == 6
    packet = read_json(Path(manifest["packets"][0]["path"]))
    finding = {
        "status": "supported",
        "values": ["1"],
        "source_quote": "synthetic product",
        "source_locator": "main paragraph",
        "reason": "Literal source evidence",
    }
    fields = {f: deepcopy(finding) for f in ("price", "sku", "availability")}
    review = {
        "cases": [
            {
                "case_id": x["case_id"],
                "main_product": "synthetic product",
                "source_fields": deepcopy(fields),
                "reference": deepcopy(fields),
                "outputs": [
                    {"method": o["method"], "fields": deepcopy(fields), "projection_issue": ""}
                    for o in x["outputs"]
                ],
            }
            for x in packet["cases"]
        ]
    }
    validate(review, packet)
    missing = deepcopy(review)
    missing["cases"][0]["outputs"].pop()
    with pytest.raises(ValueError, match="every method"):
        validate(missing, packet)
    missing = deepcopy(review)
    missing["cases"][0]["reference"].pop("sku")
    with pytest.raises(ValueError, match="every field|schema fields"):
        validate(missing, packet)
    missing = deepcopy(review)
    missing["cases"][0]["source_fields"]["price"]["source_quote"] = "invented quote"
    with pytest.raises(ValueError, match="not literal"):
        validate(missing, packet)


def test_cloudflare_url_repair_keeps_frozen_html_identity_and_declared_schema():
    from harness.web_fetch.providers.http_client import HttpResponse
    from harness.web_scraper.pipeline import SCHEMA, provider_call

    options = {
        "input_route": "url",
        "setJavaScriptEnabled": False,
        "rejectResourceTypes": ["script", "image"],
    }
    with (
        patch.dict("os.environ", {"CLOUDFLARE_ACCOUNT_ID": "test", "CLOUDFLARE_API_TOKEN": "test"}),
        patch(
            "harness.web_scraper.pipeline.request",
            return_value=HttpResponse(
                200, "https://api.cloudflare.com", {}, b'{"success":true,"result":{"sku":"X"}}'
            ),
        ) as request,
    ):
        result = provider_call(
            "cloudflare-json",
            "<h1>Frozen</h1>",
            "https://original.example",
            "https://frozen.example/hash",
            native_options=options,
        )
    body = request.call_args.kwargs["json_body"]
    assert "html" not in body and "input_route" not in body
    assert body["url"] == "https://frozen.example/hash"
    assert body["response_format"]["json_schema"] == SCHEMA
    assert result["request"]["html_sha256"] == digest(b"<h1>Frozen</h1>")
    assert result["request"]["input"] == "url"


@pytest.mark.parametrize("boundary", ["contract.json", "fixture-before.json", "snapshot"])
def test_repair_initialization_recovers_before_any_dispatch(tmp_path, boundary):
    import harness.web_scraper.repair as repair

    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "partial-init"
    save = repair.save_json
    write = Path.write_bytes
    interrupted = False

    def checkpoint(path, value, **kwargs):
        nonlocal interrupted
        save(path, value, **kwargs)
        if path.name == boundary and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    def snapshot(path, data):
        nonlocal interrupted
        value = write(path, data)
        if boundary == "snapshot" and path.parent.name == "execution" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return value

    with (
        patch.object(repair, "validate_dataset"),
        patch.object(repair, "load_runtime_env"),
        patch.object(repair, "score_rows", return_value={}),
        patch.object(
            repair,
            "provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "ok"}}},
        ) as calls,
    ):
        with (
            patch.object(repair, "save_json", side_effect=checkpoint),
            patch.object(Path, "write_bytes", snapshot),
        ):
            with pytest.raises(KeyboardInterrupt):
                run(home, profile, "cloudflare-json", target)
        calls.assert_not_called()
        run(home, profile, "cloudflare-json", target)
        assert calls.call_count == 1
        # The completed fast path must validate the requested method, even under the same profile.
        with pytest.raises(ValueError, match="another method"):
            run(home, profile, "zyte-product", target)
        assert calls.call_count == 1


def test_sibling_repair_cannot_supersede_current_success(tmp_path):
    home, source, profile, rows = parent_inputs(tmp_path)
    response = {"http_status": 200, "response": {"result": {"sku": "new"}}}
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch("harness.web_scraper.repair.provider_call", return_value=response),
    ):
        siblings = []
        for name in ["a", "b"]:
            directory = tmp_path / name
            run(home, profile, "cloudflare-json", directory)
            siblings.append(
                {
                    "path": str(directory),
                    "sha256": digest((directory / "certificate.json").read_bytes()),
                }
            )
        enriched = deepcopy(rows)
        enriched[-1]["physical_attempts"] = [deepcopy(rows[-1])]
        current, _ = compose(home, source, enriched, siblings[0])
        with pytest.raises(ValueError, match="predecessor|successful"):
            compose(home, source, current, siblings[1])


@pytest.mark.parametrize("method", ["firecrawl-json", "browserbase-json"])
def test_repair_url_route_is_qualified_and_snapshot_bound(tmp_path, method):
    home, source, profile, rows = parent_inputs(tmp_path, method=method)
    cid = rows[-1]["case_id"]
    fixture = [
        {
            "case_id": cid,
            "url": "https://frozen.example/" + cid,
            "sha256": digest((home / "dataset/html" / (cid + ".html")).read_bytes()),
        }
    ]
    target = tmp_path / "url-repair"
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch("harness.web_scraper.repair.fixture_check", return_value=fixture) as qualify,
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "ok"}}},
        ) as call,
    ):
        run(home, profile, method, target)
        assert qualify.call_count == 2
        assert call.call_args.args[3] == "https://frozen.example/" + cid
        revision = {
            "path": str(target),
            "sha256": digest((target / "certificate.json").read_bytes()),
        }
        compose(home, source, rows, revision)


def test_repair_retains_preexisting_access_retry_time(tmp_path):
    home, source, profile, rows = parent_inputs(
        tmp_path,
        failed_overrides={
            "elapsed_seconds": 10,
            "retry_execution_seconds": 2,
            "retry_missing_timers": 1,
        },
    )
    target = tmp_path / "repair"
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "ok"}}},
        ),
    ):
        run(home, profile, "cloudflare-json", target)
        current, _ = compose(
            home,
            source,
            rows,
            {"path": str(target), "sha256": digest((target / "certificate.json").read_bytes())},
        )
    assert current[-1]["retry_execution_seconds"] == 12
    assert current[-1]["retry_missing_timers"] == 1


def test_relative_review_work_creates_absolute_packet_and_source_paths(tmp_path, monkeypatch):
    home = create(tmp_path / "example")
    monkeypatch.chdir(tmp_path)
    manifest = prepare(
        home.relative_to(tmp_path),
        (home / "content.json").relative_to(tmp_path),
        Path("relative-review"),
    )
    assert all(Path(p["path"]).is_absolute() for p in manifest["packets"])
    for p in manifest["packets"]:
        for case in read_json(Path(p["path"]))["cases"]:
            assert Path(case["source_html"]).is_absolute()
            assert Path(case["source_outline"]).is_absolute()


def test_repair_resumes_access_denial_with_one_new_call_and_keeps_usage(tmp_path):
    home, source, profile, rows = parent_inputs(tmp_path)
    target = tmp_path / "quota-repair"
    denial = {"http_status": 402, "response": {"error": "Insufficient credits"}}
    success = {"http_status": 200, "response": {"result": {"sku": "new"}}}
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch("harness.web_scraper.repair.provider_call", side_effect=[denial, success]) as call,
    ):
        with pytest.raises(PermissionError, match="native access denial"):
            run(home, profile, "cloudflare-json", target)
        assert not (target / "certificate.json").exists()
        run(home, profile, "cloudflare-json", target)
        run(home, profile, "cloudflare-json", target)
        assert call.call_count == 2
        manifest = read_json(target / "manifest.json")
        assert len(manifest["calls"]) == 1 and len(manifest["physical_calls"]) == 2
        current, _ = compose(
            home,
            source,
            rows,
            {"path": str(target), "sha256": digest((target / "certificate.json").read_bytes())},
        )
    assert current[-1]["compatibility"] == "structured_output"
    assert len(current[-1]["physical_attempts"]) == 3
    assert current[-1]["physical_attempts"][1]["http_status"] == 402
    # A self-consistent certificate still cannot select the first failed attempt.
    manifest = read_json(target / "manifest.json")
    manifest["calls"][0] = manifest["physical_calls"][0]
    save_json(target / "manifest.json", manifest)
    certificate = read_json(target / "certificate.json")
    certificate["manifest_sha256"] = digest((target / "manifest.json").read_bytes())
    save_json(target / "certificate.json", certificate)
    with pytest.raises(ValueError, match="final physical attempt"):
        compose(
            home,
            source,
            rows,
            {"path": str(target), "sha256": digest((target / "certificate.json").read_bytes())},
        )


def test_repair_missing_credentials_never_consumes_a_call(tmp_path, monkeypatch):
    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "no-key"
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN")
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.provider_call") as call,
    ):
        for _ in range(3):
            with pytest.raises(PermissionError, match="Missing credentials"):
                run(home, profile, "cloudflare-json", target)
        call.assert_not_called()
        assert not (target / "calls.journal.jsonl").exists()
        assert not (target / "certificate.json").exists()


def test_repair_keeps_parent_fixture_suffix(tmp_path):
    home, _, profile, rows = parent_inputs(tmp_path, method="firecrawl-json")
    plan = read_json(profile)
    plan["participants"]["firecrawl-json"]["fixture_suffix"] = ".html"
    save_json(profile, plan)
    cid = rows[-1]["case_id"]
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch("harness.web_scraper.repair.fixture_check", return_value=[]) as qualify,
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={"http_status": 200, "response": {"data": {"json": {"sku": "ok"}}}},
        ) as call,
    ):
        run(home, profile, "firecrawl-json", tmp_path / "suffix")
    assert call.call_args.args[3].endswith("/" + cid + ".html")
    assert qualify.call_args.args[-1] == ".html"


@pytest.mark.parametrize("change", ["profile", "dataset"])
def test_repair_rejects_changed_inputs_before_certification(tmp_path, change):
    from harness.web_scraper.pipeline import validate_dataset

    home, _, profile, rows = parent_inputs(tmp_path)
    target = tmp_path / "mutated"

    def provider(*args, **kwargs):
        if change == "profile":
            plan = read_json(profile)
            plan["timeout_seconds"] = 119
            save_json(profile, plan)
        else:
            source = home / "dataset/html" / (rows[-1]["case_id"] + ".html")
            source.write_text(
                source.read_text(encoding="utf-8") + "<!-- changed -->", encoding="utf-8"
            )
        return {"http_status": 200, "response": {"result": {"sku": "ok"}}}

    with (
        patch(
            "harness.web_scraper.repair.validate_dataset",
            side_effect=lambda path: validate_dataset(path, profile=home / "profile.json"),
        ),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.provider_call", side_effect=provider),
    ):
        with pytest.raises(ValueError, match="changed|mismatch"):
            run(home, profile, "cloudflare-json", target)
    assert not (target / "certificate.json").exists()
    assert len(list((target / "calls").glob("*.json"))) == 1


def test_repair_does_not_dispatch_a_page_changed_before_its_turn(tmp_path):
    home, _, profile, rows = parent_inputs(tmp_path, success_count=1)
    target = tmp_path / "changed-next"

    def provider(*args, **kwargs):
        source = home / "dataset/html" / (rows[-1]["case_id"] + ".html")
        source.write_text(source.read_text(encoding="utf-8") + "<!-- changed -->", encoding="utf-8")
        return {"http_status": 200, "response": {"result": {"sku": "ok"}}}

    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.provider_call", side_effect=provider) as calls,
    ):
        with pytest.raises(ValueError, match="changed before dispatch"):
            run(home, profile, "cloudflare-json", target)
        assert calls.call_count == 1
    assert not (target / "certificate.json").exists()


def test_local_baseline_repair_uses_selected_dataset_home(tmp_path):
    home, _, profile, _ = parent_inputs(tmp_path, method="extruct")
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "ok"}}},
        ) as call,
    ):
        run(home, profile, "extruct", tmp_path / "local-repair")
    assert call.call_args.kwargs["home"] == home


@pytest.mark.parametrize(
    "invalid", [{"status": "ambigous"}, {"values": "not a list"}, {"values": [4]}]
)
def test_persisted_review_rejects_invalid_assessment_schema(tmp_path, invalid):
    home = create(tmp_path / "example")
    work = tmp_path / "audit"
    manifest = prepare(home, home / "content.json", work)
    packet = read_json(Path(manifest["packets"][0]["path"]))
    finding = {
        "status": "supported",
        "values": [],
        "source_quote": "synthetic product",
        "source_locator": "p",
        "reason": "source evidence",
    }
    cases = []
    for case in packet["cases"]:
        fields = {f: deepcopy(finding) for f in ("price", "sku", "availability")}
        cases.append(
            {
                "case_id": case["case_id"],
                "main_product": "synthetic product",
                "source_fields": deepcopy(fields),
                "reference": deepcopy(fields),
                "outputs": [
                    {"method": o["method"], "fields": deepcopy(fields), "projection_issue": ""}
                    for o in case["outputs"]
                ],
            }
        )
    value = {"cases": cases}
    value["cases"][0]["outputs"][0]["fields"]["sku"]["values"] = [""]
    validate(value, packet)
    value["cases"][0]["reference"]["sku"].update(invalid)
    with pytest.raises(ValueError, match="schema"):
        validate(value, packet)


def test_audit_interrupt_stops_sessions_before_waiting_for_workers(tmp_path):
    from harness.web_scraper.review import run as run_audit

    packet = tmp_path / "queued.json"
    save_json(packet, {})
    save_json(
        tmp_path / "manifest.json",
        {"packets": [{"path": str(packet), "sha256": digest(packet.read_bytes())}], "bindings": {}},
    )
    save_json(tmp_path / "schema.json", {})
    events = []

    class Sessions:
        def stop(self):
            events.append("stop")

    class Future:
        def result(self):
            raise KeyboardInterrupt

        def cancel(self):
            events.append("cancel")

    class Executor:
        def __init__(self, **kwargs):
            pass

        def submit(self, *args):
            return Future()

        def shutdown(self, **kwargs):
            assert kwargs == {"wait": True, "cancel_futures": True}
            events.append("shutdown")

    with (
        patch("harness.web_scraper.review.ReviewerSessions", Sessions),
        patch("harness.web_scraper.review.ThreadPoolExecutor", Executor),
        pytest.raises(KeyboardInterrupt),
    ):
        run_audit(tmp_path, model="synthetic")
    assert events.index("stop") < events.index("cancel") < events.index("shutdown")


def test_repair_clock_can_cross_cooldown_deadline(tmp_path):
    home, _, profile, _ = parent_inputs(tmp_path, success_count=0)
    clock = [0.0]
    sleeps = []

    def now():
        clock[0] += 0.75
        return clock[0]

    def sleep(seconds):
        assert seconds >= 0
        sleeps.append(seconds)
        clock[0] += seconds

    plan = read_json(profile)
    participant = plan["participants"]["cloudflare-json"]
    participant["minimum_call_interval_seconds"] = 1
    participant["post_call_cooldown_seconds"] = 1
    save_json(profile, plan)
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch("harness.web_scraper.repair.time.monotonic", side_effect=now),
        patch("harness.web_scraper.repair.time.sleep", side_effect=sleep),
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={
                "state": "response_received",
                "response": {"result": {"sku": "recovered"}},
                "http_status": 200,
            },
        ) as provider,
    ):
        run(home, profile, "cloudflare-json", tmp_path / "repaired")
    assert provider.call_count == 3 and sleeps


def test_compound_citation_cannot_discard_a_fabricated_price(tmp_path):
    source = tmp_path / "source.html"
    source.write_text("Product details. Actual price: 13.40", encoding="utf-8")
    packet = {
        "cases": [
            {
                "case_id": "one",
                "source_html": str(source),
                "source_outline": str(source),
                "outputs": [],
            }
        ]
    }
    for quote in ("Product details; Price: 999", "Product details; 999"):
        raw = review_payload(packet, quote)
        raw["cases"][0]["source_fields"]["price"]["values"] = ["999"]
        result = reconcile_citations(raw, packet)
        assert result["cases"][0]["source_fields"]["price"]["source_quote"] == quote
        assert "citation_format_corrections" not in result
        with pytest.raises(ValueError, match="not literal"):
            validate(result, packet)
    raw = review_payload(packet, "Product details; Actual price: 13.40")
    result = reconcile_citations(raw, packet)
    assert result["cases"][0]["source_fields"]["price"]["source_quote"] == "Actual price: 13.40"


def test_repair_composition_accepts_verified_field_projection(tmp_path):
    home, source, profile, rows = parent_inputs(tmp_path)
    destination = tmp_path / "repaired"
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={
                "state": "response_received",
                "http_status": 200,
                "response": {"result": {"sku": "fixed"}},
            },
        ),
    ):
        run(home, profile, "cloudflare-json", destination)
        projected = deepcopy(rows)
        for row in projected:
            row["collected_normalized_fields"] = row["normalized_fields"]
            row["normalized_fields"] = {"sku": "offline projection"}
            row["projection_revision"] = {"pipeline_sha256": "synthetic"}
        revision = {
            "path": str(destination),
            "sha256": digest((destination / "certificate.json").read_bytes()),
        }
        current, _ = compose(home, source, projected, revision)
    assert current[0]["normalized_fields"] == {"sku": "offline projection"}
    assert current[-1]["normalized_fields"]["sku"] == "fixed"


@pytest.mark.parametrize("limit", [0, -1])
def test_review_rejects_nonpositive_limit_before_preparation_or_dispatch(tmp_path, limit):
    from harness.web_scraper.cli import main
    from harness.web_scraper.review import run as run_audit

    with patch("harness.web_scraper.review.ReviewerSessions.start") as start:
        with pytest.raises(ValueError, match="positive integer"):
            run_audit(tmp_path / "absent", model="synthetic", limit=limit)
        start.assert_not_called()
    with (
        patch("harness.web_scraper.review.prepare") as prepare_audit,
        patch("harness.web_scraper.review.run") as dispatch,
        pytest.raises(SystemExit) as error,
    ):
        main(
            [
                "review",
                "--content",
                str(tmp_path / "absent.json"),
                "--work",
                str(tmp_path / "work"),
                "--model",
                "synthetic",
                "--limit",
                str(limit),
            ]
        )
    assert error.value.code == 2
    prepare_audit.assert_not_called()
    dispatch.assert_not_called()
    assert not (tmp_path / "work").exists()


def test_initial_audit_can_start_with_already_repaired_results(tmp_path):
    from harness.web_scraper.review import assemble

    home, source, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "repaired"
    content = tmp_path / "current-content.json"
    initial = tmp_path / "initial"
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={
                "state": "response_received",
                "http_status": 200,
                "response": {"result": {"sku": "new"}},
            },
        ),
    ):
        run(home, profile, "cloudflare-json", target)
        save_json(
            content,
            {
                "evidence_runs": [
                    {
                        "run": str(source),
                        "repair": [
                            {
                                "path": str(target),
                                "sha256": digest((target / "certificate.json").read_bytes()),
                            }
                        ],
                    }
                ]
            },
        )
        manifest = prepare(home, content, initial)
        for entry in manifest["packets"]:
            path = Path(entry["path"])
            packet = read_json(path)
            # A legacy unrepaired item requires fallback extraction-manifest
            # lookup while its repaired neighbor uses the explicit call identity.
            packet["cases"][0]["outputs"][0].pop("execution_identity")
            save_json(path, packet)
            entry["sha256"] = digest(path.read_bytes())
            finding = {
                "status": "supported",
                "values": [],
                "source_quote": "synthetic product",
                "source_locator": "p",
                "reason": "Synthetic source context",
            }
            fields = {field: deepcopy(finding) for field in ("price", "sku", "availability")}
            save_json(
                initial / "reviews" / path.name,
                {
                    "cases": [
                        {
                            "case_id": c["case_id"],
                            "main_product": "Synthetic",
                            "source_fields": fields,
                            "reference": fields,
                            "outputs": [
                                {"method": o["method"], "fields": fields, "projection_issue": ""}
                                for o in c["outputs"]
                            ],
                        }
                        for c in packet["cases"]
                    ]
                },
            )
        save_json(initial / "manifest.json", manifest)
        save_json(initial / "reviewer.json", {"model": "synthetic"})
        for name in ("supplemental", "adjudication"):
            save_json(
                tmp_path / name / "manifest.json",
                {"bindings": {}, "packets": [], "case_count": 0, "result_count": 0},
            )
        result = assemble(
            home,
            content,
            initial,
            tmp_path / "supplemental",
            tmp_path / "adjudication",
            tmp_path / "complete.json",
            profile=home / "profile.json",
        )
    assert result["result_count"] == 3
    assert result["supplemental_results_reviewed"] == 0
    assert {r["basis"] for r in result["results"].values()} == {"original"}
    from harness.web_scraper.review import run as run_audit

    unchanged = tmp_path / "unchanged-supplement"
    with (
        patch("harness.web_scraper.repair.score_rows", return_value={}),
        patch("harness.web_scraper.review.ReviewerSessions.start") as dispatch,
    ):
        supplemental = prepare(home, content, unchanged, prior=initial)
        assert supplemental["result_count"] == 0 and supplemental["packets"] == []
        assert run_audit(unchanged, model="synthetic")["packets"] == 0
        again = assemble(
            home,
            content,
            initial,
            unchanged,
            tmp_path / "adjudication",
            tmp_path / "again.json",
            profile=home / "profile.json",
        )
        assert again["supplemental_results_reviewed"] == 0
        dispatch.assert_not_called()


@pytest.mark.parametrize(
    "malformed",
    ["null", "list", "missing-fields", "unknown-correction", "unknown-whitespace-correction"],
)
def test_malformed_raw_review_is_quarantined_and_resume_completes(tmp_path, malformed):
    from types import SimpleNamespace

    from harness.web_scraper.review import run as run_audit

    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work)
    packet = read_json(Path(manifest["packets"][0]["path"]))
    raw = work / "raw/000.json"
    correction = review_payload(packet)
    correction["citation_format_corrections"] = [
        {
            "case_id": "outside-packet",
            "original_quote": "synthetic; product",
            "literal_quote": "synthetic",
            "rule": "whitespace-only"
            if malformed == "unknown-whitespace-correction"
            else "literal-fragment-selection",
        }
    ]
    bad = {
        "unknown-correction": correction,
        "unknown-whitespace-correction": correction,
        "null": None,
        "list": [],
        "missing-fields": {"cases": [{"case_id": packet["cases"][0]["case_id"]}]},
    }[malformed]
    save_json(raw, bad)
    original = raw.read_bytes()

    def start(command, **kwargs):
        save_json(Path(command[command.index("--output-last-message") + 1]), review_payload(packet))
        return SimpleNamespace(returncode=0)

    with (
        patch("harness.web_scraper.review.ReviewerSessions.start", side_effect=start) as dispatch,
        patch("harness.web_scraper.review.ReviewerSessions.communicate"),
    ):
        result = run_audit(work, model="synthetic", workers=1)
        assert result["needs_correction"] == [] and result["cases"] == 3
        assert dispatch.call_count == 1
        assert len(list((work / "invalid").glob("*.json"))) == 1
        assert next((work / "invalid").glob("*.json")).read_bytes() == original
        assert (work / "reviews/000.json").exists()
        run_audit(work, model="synthetic", workers=1)
        assert dispatch.call_count == 1


def test_supplemental_review_selects_only_changed_repaired_call(tmp_path):
    from harness.web_scraper.review import assemble

    home, source, profile, rows = parent_inputs(tmp_path, success_count=1)
    first = tmp_path / "first-repair"
    initial = tmp_path / "initial"
    content = tmp_path / "content-first.json"
    success = {
        "state": "response_received",
        "http_status": 200,
        "response": {"result": {"sku": "recovered"}},
    }
    failure = {
        "state": "response_received",
        "http_status": 500,
        "response": {"message": "Transient failure"},
    }
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.score_rows", return_value={}),
    ):
        with patch("harness.web_scraper.repair.provider_call", side_effect=[success, failure]):
            run(home, profile, "cloudflare-json", first)
        revisions = [
            {"path": str(first), "sha256": digest((first / "certificate.json").read_bytes())}
        ]
        save_json(content, {"evidence_runs": [{"run": str(source), "repair": revisions}]})
        manifest = prepare(home, content, initial)
        for entry in manifest["packets"]:
            packet = read_json(Path(entry["path"]))
            save_json(initial / "reviews" / Path(entry["path"]).name, review_payload(packet))
        save_json(initial / "reviewer.json", {"model": "synthetic"})
        second_profile = tmp_path / "second-plan.json"
        create_plan(home, content, second_profile, {}, "https://frozen.example")
        second = tmp_path / "second-repair"
        with patch("harness.web_scraper.repair.provider_call", return_value=success) as extract:
            run(home, second_profile, "cloudflare-json", second)
        assert extract.call_count == 1
        revisions.append(
            {"path": str(second), "sha256": digest((second / "certificate.json").read_bytes())}
        )
        save_json(content, {"evidence_runs": [{"run": str(source), "repair": revisions}]})
        supplement = tmp_path / "supplement"
        supplemental = prepare(home, content, supplement, prior=initial)
        assert supplemental["case_count"] == supplemental["result_count"] == 1
        packet = read_json(Path(supplemental["packets"][0]["path"]))
        assert packet["cases"][0]["case_id"] == rows[-1]["case_id"]
        save_json(supplement / "reviews/000.json", review_payload(packet))
        adjudication = tmp_path / "adjudication"
        save_json(
            adjudication / "manifest.json",
            {"bindings": {}, "packets": [], "case_count": 0, "result_count": 0},
        )
        result = assemble(
            home,
            content,
            initial,
            supplement,
            adjudication,
            tmp_path / "complete.json",
            profile=home / "profile.json",
        )
    assert result["result_count"] == 3 and result["supplemental_results_reviewed"] == 1
    assert [r["basis"] for r in result["results"].values()].count("supplemental") == 1


@pytest.mark.parametrize("workers", [0, -1])
def test_adjudication_invalid_workers_leave_no_workspace(tmp_path, workers):
    from harness.web_scraper.cli import main

    work = tmp_path / "adjudication"
    with (
        patch("harness.web_scraper.review.prepare_adjudication") as prepare_audit,
        patch("harness.web_scraper.review.run") as dispatch,
        pytest.raises(SystemExit) as error,
    ):
        main(
            [
                "adjudicate",
                "--prior",
                str(tmp_path / "prior"),
                "--work",
                str(work),
                "--rules",
                str(tmp_path / "rules.json"),
                "--model",
                "synthetic",
                "--workers",
                str(workers),
            ]
        )
    assert error.value.code == 2
    prepare_audit.assert_not_called()
    dispatch.assert_not_called()
    assert not work.exists()


def test_limited_review_resume_advances_without_repeating_completed_packets(tmp_path):
    from types import SimpleNamespace

    from harness.web_scraper.review import run as run_audit

    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work, batch_size=1)
    assert len(manifest["packets"]) == 3
    dispatched = []

    def start(command, **kwargs):
        raw = Path(command[command.index("--output-last-message") + 1])
        packet = read_json(work / "packets" / raw.name)
        dispatched.append(raw.name)
        save_json(raw, review_payload(packet))
        return SimpleNamespace(returncode=0)

    with (
        patch("harness.web_scraper.review.ReviewerSessions.start", side_effect=start),
        patch("harness.web_scraper.review.ReviewerSessions.communicate"),
    ):
        for expected in (1, 2, 3, 3):
            result = run_audit(work, model="synthetic", workers=1, limit=1)
            assert result["packets"] == expected
            assert result["needs_correction"] == []
        assert dispatched == ["000.json", "001.json", "002.json"]
        # Completed evidence still needs validation on a bounded resume.
        target = work / "reviews/000.json"
        save_json(target, {"cases": []})
        with pytest.raises(ValueError):
            run_audit(work, model="synthetic", workers=1, limit=1)
        assert len(dispatched) == 3


@pytest.mark.parametrize("duplicate", ["same-run", "overlapping-run"])
def test_review_rejects_duplicate_executions_before_freezing_packets(tmp_path, duplicate):
    import shutil

    home = create(tmp_path / "example")
    content = read_json(home / "content.json")
    selection = deepcopy(content["evidence_runs"][0])
    if duplicate == "overlapping-run":
        source = home / "runs" / selection["run"]
        copied = home / "runs" / "overlapping"
        shutil.copytree(source, copied)
        selection["run"] = "overlapping"
    content["evidence_runs"].append(selection)
    save_json(home / "duplicate.json", content)
    work = tmp_path / "review"
    with (
        patch("harness.web_scraper.review.ReviewerSessions.start") as dispatch,
        pytest.raises(ValueError, match="duplicate case-method"),
    ):
        prepare(home, home / "duplicate.json", work)
    dispatch.assert_not_called()
    assert not (work / "manifest.json").exists()
    assert not (work / "packets").exists()


@pytest.mark.parametrize("mutation", ["wrong-method", "incomplete-journal"])
def test_repair_resume_validates_partial_history_before_dispatch(tmp_path, mutation):
    import json

    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "paused-repair"
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch(
            "harness.web_scraper.repair.provider_call",
            return_value={"http_status": 402, "response": {"error": "Insufficient credits"}},
        ),
        pytest.raises(PermissionError),
    ):
        run(home, profile, "cloudflare-json", target)
    journal = target / "calls.journal.jsonl"
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    if mutation == "wrong-method":
        path = target / entries[0]["path"]
        row = read_json(path)
        row["method"] = "another-method"
        save_json(path, row)
        entries[0]["sha256"] = digest(path.read_bytes())
    raw = "".join(json.dumps(row) + "\n" for row in entries)
    journal.write_text(
        raw.rstrip("\n") if mutation == "incomplete-journal" else raw, encoding="utf-8"
    )
    with (
        patch("harness.web_scraper.repair.validate_dataset"),
        patch("harness.web_scraper.repair.load_runtime_env"),
        patch("harness.web_scraper.repair.provider_call") as dispatch,
        pytest.raises(ValueError, match="scope|journal"),
    ):
        run(home, profile, "cloudflare-json", target)
    dispatch.assert_not_called()
    assert not (target / "certificate.json").exists()


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5, "2", None])
@pytest.mark.parametrize("adjudication", [False, True])
def test_review_batch_size_is_validated_before_any_io(tmp_path, batch_size, adjudication):
    from harness.web_scraper.review import prepare, prepare_adjudication

    work = tmp_path / "new-work"
    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        if adjudication:
            prepare_adjudication(
                tmp_path / "missing", work, tmp_path / "rules", batch_size=batch_size
            )
        else:
            prepare(tmp_path / "missing", tmp_path / "content", work, batch_size=batch_size)
    assert not work.exists()


@pytest.mark.parametrize("field", ["price", "sku", "availability"])
def test_complete_field_exclusion_assembles_and_builds_unavailable_metrics(tmp_path, field):
    from harness.web_scraper.report import build, check
    from harness.web_scraper.review import assemble, prepare, prepare_adjudication

    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work)
    for entry in manifest["packets"]:
        payload = review_payload(read_json(Path(entry["path"])))
        for case in payload["cases"]:
            case["reference"][field]["status"] = "ambiguous"
        save_json(work / "reviews" / Path(entry["path"]).name, payload)
    save_json(work / "reviewer.json", {"model": "synthetic-test"})
    for name in ("supplemental", "adjudication"):
        save_json(
            tmp_path / name / "manifest.json",
            {"bindings": {}, "packets": [], "case_count": 0, "result_count": 0},
        )
    (tmp_path / "adjudication/manifest.json").unlink()
    adjudication = prepare_adjudication(work, tmp_path / "adjudication", home / "profile.json")
    for entry in adjudication["packets"]:
        payload = review_payload(read_json(Path(entry["path"])))
        for case in payload["cases"]:
            case["reference"][field]["status"] = "ambiguous"
        save_json(tmp_path / "adjudication/reviews" / Path(entry["path"]).name, payload)
    audit_path = tmp_path / "audit.json"
    audit = assemble(
        home,
        home / "content.json",
        work,
        tmp_path / "supplemental",
        tmp_path / "adjudication",
        audit_path,
        profile=home / "profile.json",
    )
    for scores in audit["audited_scores"].values():
        for excluded in [field, "OutOfStock"] if field == "availability" else [field]:
            assert scores[excluded] == {"precision": None, "recall": None, "f1": None, "cases": 0}
    content = read_json(home / "content.json")
    content["audit"] = {"path": str(audit_path), "sha256": digest(audit_path.read_bytes())}
    save_json(home / "content.json", content)
    build(
        tmp_path / "audited-report", home / "content.json", home=home, profile=home / "profile.json"
    )
    check(tmp_path / "audited-report")
