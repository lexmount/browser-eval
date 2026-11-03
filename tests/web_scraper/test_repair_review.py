"""Failure-only correction and complete source-review behavior contracts."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_scraper.example import create
from harness.web_scraper.pipeline import digest, read_json, save_json
from harness.web_scraper.repair import compose, create_plan, run
from harness.web_scraper.review import prepare, reconcile_citations, validate


def parent_inputs(tmp_path):
    home = create(tmp_path / "example")
    source = home / "runs/parent"
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    cases = sorted(truth)
    contract = {"methods": ["cloudflare-json"], "case_ids": cases}
    save_json(source / "contract.json", contract)
    entries, rows = [], []
    for i, cid in enumerate(cases):
        row = {
            "method": "cloudflare-json",
            "case_id": cid,
            "input_sha256": digest((home / "dataset/html" / (cid + ".html")).read_bytes()),
            "http_status": 200 if i < 2 else None,
            "compatibility": "structured_output" if i < 2 else "no_structured_output",
            "normalized_fields": {"sku": str(i)} if i < 2 else None,
            "response": {"result": {"sku": str(i)}} if i < 2 else {},
        }
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
    source.write_text("<p>Price: 13,40\u00a0€</p>")
    outline.write_text("Price: 13,40\u00a0€")
    packet = {
        "cases": [{"case_id": "one", "source_html": str(source), "source_outline": str(outline)}]
    }
    finding = {"source_quote": "13,40 €", "reason": "price"}
    raw = {
        "cases": [
            {"case_id": "one", "source_fields": {"price": finding}, "reference": {}, "outputs": []}
        ]
    }
    corrected = reconcile_citations(raw, packet)
    assert corrected["cases"][0]["source_fields"]["price"]["source_quote"] == "13,40\u00a0€"
    assert raw["cases"][0]["source_fields"]["price"]["source_quote"] == "13,40 €"
    finding["source_quote"] = "13,41 €"
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
    with pytest.raises(ValueError, match="every field"):
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
