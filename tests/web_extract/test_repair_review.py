"""Failure-only correction and complete source-review behavior contracts."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.repair import compose, create_plan, run
from harness.web_extract.review import prepare, read_packet, reconcile_citations, validate


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


@pytest.mark.parametrize("reference_status", ["ambiguous", "absent"])
def test_complete_record_audit_handles_distinct_categories_without_changing_native_fields(
    tmp_path, reference_status
):
    home = create(tmp_path / "records", mode="field-extraction-cohort")
    truth_path = home / "dataset/dataset/ground-truth.json"
    truth = read_json(truth_path)
    last = sorted(truth)[-1]
    truth[last].update(domain="movie", values={"title": ["Example"], "director": ["A"]})
    save_json(truth_path, truth)
    schemas_path = home / "dataset/schemas.json"
    schemas = read_json(schemas_path)
    schemas["movie"] = {
        "type": "object",
        "properties": {
            key: {"type": "array", "items": {"type": "string"}} for key in ("title", "director")
        },
    }
    save_json(schemas_path, schemas)
    selections = []
    retained = {}
    for method in ("cloudflare-json", "firecrawl-json"):
        run_dir = home / "runs" / ("review-" + method)
        save_json(run_dir / "manifest.json", {"synthetic": True})
        rows = []
        for cid, ref in truth.items():
            native = {key: list(value) for key, value in ref["values"].items()}
            response = (
                {"result": native} if method == "cloudflare-json" else {"data": {"json": native}}
            )
            rows.append(
                {"case_id": cid, "method": method, "response": response, "http_status": 200}
            )
        retained[str(run_dir)] = (run_dir, rows)
        selections.append({"run": str(run_dir)})
    content = home / "audit-content.json"
    save_json(content, {"evidence_runs": selections})
    work = tmp_path / "review"
    with patch(
        "harness.web_extract.repair.load_selection",
        side_effect=lambda _home, selection: retained[selection["run"]],
    ):
        manifest = prepare(home, content, work)
    assert manifest["case_count"] == 3
    assert manifest["result_count"] == 6
    packet = read_packet(Path(manifest["packets"][0]["path"]))
    opinions = {"cases": []}
    for case in packet["cases"]:
        fields = {
            field: {
                "status": "supported",
                "values": values,
                "source_quote": "Example",
                "source_locator": "p",
                "reason": "Literal source checked",
            }
            for field, values in case["reference"].items()
        }
        for output in case["outputs"]:
            assert output["normalized_fields"] == case["reference"]
        opinions["cases"].append(
            {
                "case_id": case["case_id"],
                "main_record": "Example",
                "source_fields": fields,
                "reference": fields,
                "outputs": [
                    {"method": output["method"], "fields": fields, "projection_issue": ""}
                    for output in case["outputs"]
                ],
            }
        )
    validate(opinions, packet)
    missing = deepcopy(opinions)
    missing["cases"][0]["outputs"].pop()
    with pytest.raises(ValueError, match="every method"):
        validate(missing, packet)
    wrong_fields = deepcopy(opinions)
    wrong_fields["cases"][0]["source_fields"]["price"] = wrong_fields["cases"][0][
        "source_fields"
    ].pop("title")
    with pytest.raises(ValueError, match="schema|every field"):
        validate(wrong_fields, packet)
    from harness.web_extract.review import prepare_adjudication

    disputed = deepcopy(opinions)
    field = next(iter(disputed["cases"][0]["reference"]))
    disputed["cases"][0]["reference"][field]["status"] = reference_status
    save_json(work / "reviews" / Path(manifest["packets"][0]["path"]).name, disputed)
    rules = tmp_path / "record-rules.txt"
    rules.write_text("Resolve field scope against the frozen source.", encoding="utf-8")
    adjudication = tmp_path / "adjudication"
    adjudicated = prepare_adjudication(work, adjudication, rules)
    assert adjudicated["schema"] == "record-reference-adjudication/1"
    assert adjudicated["case_count"] == 1
    second_packet = read_packet(Path(adjudicated["packets"][0]["path"]))
    assert second_packet["cases"][0]["field_schema"] == packet["cases"][0]["field_schema"]
    finding = deepcopy(disputed["cases"][0])
    finding["outputs"] = []
    validate({"cases": [finding]}, second_packet)


@pytest.fixture(autouse=True)
def native_test_credentials(monkeypatch):
    from harness.web_extract.pipeline import available_methods

    for method in available_methods().values():
        for key in method["keys"]:
            monkeypatch.setenv(key, "synthetic-test-credential")


def parent_inputs(
    tmp_path, method="cloudflare-json", failed_overrides=None, success_count=2, native_options=None
):
    home = create(tmp_path / "example")
    source = home / "runs/parent"
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    cases = sorted(truth)
    contract = {"methods": [method], "case_ids": cases}
    if native_options is not None:
        contract["native_options"] = native_options
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call", return_value=response) as provider,
    ):
        run(home, profile, "cloudflare-json", destination)
        run(home, profile, "cloudflare-json", destination)
    assert provider.call_count == 1
    assert {p: p.read_bytes() for p in original} == original
    revision = {
        "path": str(destination),
        "sha256": digest((destination / "certificate.json").read_bytes()),
    }
    current = compose(home, source, deepcopy(rows), revision)
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.pipeline.provider_call") as provider,
    ):
        with pytest.raises(ValueError, match="never successes"):
            run(home, profile, "cloudflare-json", tmp_path / "wrong")
    provider.assert_not_called()


def test_relative_home_repairs_resume_and_compose_without_repeating_calls(tmp_path, monkeypatch):
    home, source, _, rows = parent_inputs(tmp_path)
    cwd = tmp_path / "working"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    relative_home = Path("..") / home.relative_to(tmp_path)
    relative_source = Path("..") / source.relative_to(tmp_path)
    content = tmp_path / "content.json"
    profile = tmp_path / "relative-plan.json"
    create_plan(relative_home, content, profile, {}, "https://frozen.example")
    destination = tmp_path / "relative-repair"
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={
                "http_status": 200,
                "response": {"result": {"sku": "fixed"}},
            },
        ) as provider,
    ):
        run(relative_home, profile, "cloudflare-json", destination)
        run(relative_home, profile, "cloudflare-json", destination)
    assert provider.call_count == 1
    current = compose(
        relative_home,
        relative_source,
        rows,
        {
            "path": str(destination),
            "sha256": digest((destination / "certificate.json").read_bytes()),
        },
    )
    assert current[-1]["normalized_fields"]["sku"] == "fixed"


def test_ambiguous_repair_dispatch_is_not_repeated(tmp_path):
    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "interrupted"
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call", side_effect=KeyboardInterrupt
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call", side_effect=TimeoutError("network")),
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
    from harness.web_extract.analysis import field_results

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
    from harness.web_extract.pipeline import check_projection_controls, normalize

    check_projection_controls()
    assert normalize("extruct", {"result": {"InStock": "InStock"}})["availability"] == "InStock"
    ambiguous = {"InStock": "InStock", "OutOfStock": "OutOfStock"}
    assert "availability" not in normalize("extruct", {"result": ambiguous})
    assert "availability" not in normalize("extruct", {"result": {"InStock": True}})


def test_complete_audit_rejects_unreviewed_and_substituted_native_answers(tmp_path):
    from harness.web_extract.review import assemble

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
        packet = read_packet(Path(entry["path"]))
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
    from harness.web_extract.pipeline import load_run
    from harness.web_extract.report import build

    audit_path = tmp_path / "complete.json"
    content_path = home / "content.json"
    content = read_json(content_path)
    content["audit"] = {"path": str(audit_path), "sha256": digest(audit_path.read_bytes())}
    save_json(content_path, content)
    build(
        tmp_path / "reviewed-report",
        content_path,
        home=home,
        profile=home / "profile.json",
        language="en",
    )
    from bs4 import BeautifulSoup

    english_detail = next((tmp_path / "reviewed-report/en").glob("result-*.html"))
    summaries = BeautifulSoup(english_detail.read_text(encoding="utf-8"), "html.parser").select(
        "summary"
    )
    assert any(s.get_text() == "Field judgments against original annotations" for s in summaries)
    assert all("原标注" not in s.get_text() for s in summaries)
    for mutation in ("scorer", "reference", "projection", "aggregate", "accepted-reference"):
        stale = deepcopy(result)
        if mutation in ("scorer", "reference"):
            stale["scorer_sha256" if mutation == "scorer" else "official_reference_sha256"] = (
                "0" * 64
            )
            error = "reference or scorer binding"
        elif mutation == "projection":
            next(iter(stale["results"].values()))["audited_fields"]["sku"]["actual"] = (
                "stale projection"
            )
            error = "field judgments"
        elif mutation == "accepted-reference":
            next(iter(stale["audited_reference"].values()))["sku"] = ["different accepted value"]
            error = "field judgments"
        else:
            next(iter(stale["audited_scores"].values()))["sku"]["f1"] = 0.1234567
            error = "audit aggregates"
        stale_path = tmp_path / (mutation + ".json")
        save_json(stale_path, stale)
        content["audit"] = {"path": str(stale_path), "sha256": digest(stale_path.read_bytes())}
        save_json(content_path, content)
        with pytest.raises(ValueError, match=error):
            build(
                tmp_path / (mutation + "-report"),
                content_path,
                home=home,
                profile=home / "profile.json",
            )
    content["audit"] = {"path": str(audit_path), "sha256": digest(audit_path.read_bytes())}
    save_json(content_path, content)
    with patch("harness.web_extract.pipeline.normalize", return_value={"sku": "new projection"}):
        with pytest.raises(ValueError, match="field judgments.*current native projection"):
            build(
                tmp_path / "changed-projection",
                content_path,
                home=home,
                profile=home / "profile.json",
            )
    finding = next(iter(result["results"].values()))
    assert finding["reviewer_fields"]["sku"]["status"] == "supported"
    assert finding["field_reconciliation"]["sku"]["resolution"]
    source_run = home / "runs/example-v1"
    _, original_rows = load_run(source_run)
    substituted = deepcopy(original_rows)
    failed = next(row for row in substituted if row["method"] == "synthetic-blocked")
    failed["error"] = "A different runtime failure with the same empty response"
    with patch(
        "harness.web_extract.report.load_run",
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
    from harness.web_extract.review import prepare_adjudication

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
        packet = read_packet(Path(entry["path"]))
        opinions = []
        for case in packet["cases"]:
            opinion = deepcopy(prior["cases"][0])
            opinion["case_id"] = case["case_id"]
            opinion["outputs"] = []
            opinion["reference"]["sku"]["status"] = "supported"
            opinion["source_fields"]["sku"]["values"] = ["synthetic product"]
            opinions.append(opinion)
        save_json(adjudication_work / "reviews" / Path(entry["path"]).name, {"cases": opinions})
    from harness.web_extract.review import completed

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
    packet = read_packet(path)
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
    packet = read_packet(Path(manifest["packets"][0]["path"]))
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
    from harness.web_extract.pipeline import SCHEMA, provider_call
    from harness.web_fetch.providers.http_client import HttpResponse

    options = {
        "input_route": "url",
        "setJavaScriptEnabled": False,
        "rejectResourceTypes": ["script", "image"],
    }
    with (
        patch.dict("os.environ", {"CLOUDFLARE_ACCOUNT_ID": "test", "CLOUDFLARE_API_TOKEN": "test"}),
        patch(
            "harness.web_extract.pipeline.request",
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


@pytest.mark.parametrize("boundary", ["contract.json", "fixture-before.json"])
def test_repair_initialization_recovers_before_any_dispatch(tmp_path, boundary):
    import harness.web_extract.repair as repair

    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "partial-init"
    save = repair.save_json
    interrupted = False

    def checkpoint(path, value, **kwargs):
        nonlocal interrupted
        save(path, value, **kwargs)
        if path.name == boundary and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    with (
        patch.object(repair, "validate_dataset"),
        patch.object(repair, "load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "ok"}}},
        ) as calls,
    ):
        with (
            patch.object(repair, "save_json", side_effect=checkpoint),
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call", return_value=response),
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
        current = compose(home, source, enriched, siblings[0])
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.repair.fixture_check", return_value=fixture) as qualify,
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "ok"}}},
        ) as call,
    ):
        run(home, profile, method, target)
        assert qualify.call_count == 2
        assert call.call_args.args[3] == "https://frozen.example/" + cid + ".html"
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "ok"}}},
        ),
    ):
        run(home, profile, "cloudflare-json", target)
        current = compose(
            home,
            source,
            rows,
            {"path": str(target), "sha256": digest((target / "certificate.json").read_bytes())},
        )
    assert current[-1]["retry_execution_seconds"] == 12
    assert current[-1]["retry_missing_timers"] == 1


def test_relative_review_work_stores_portable_paths_and_resolves_sources(tmp_path, monkeypatch):
    home = create(tmp_path / "example")
    monkeypatch.chdir(tmp_path)
    manifest = prepare(
        home.relative_to(tmp_path),
        (home / "content.json").relative_to(tmp_path),
        Path("relative-review"),
    )
    assert all(Path(p["path"]).is_absolute() for p in manifest["packets"])
    for p in manifest["packets"]:
        for case in read_packet(Path(p["path"]))["cases"]:
            assert Path(case["source_html"]).is_absolute()
            assert Path(case["source_outline"]).is_absolute()


def test_repair_resumes_access_denial_with_one_new_call_and_keeps_usage(tmp_path):
    home, source, profile, rows = parent_inputs(tmp_path)
    target = tmp_path / "quota-repair"
    denial = {"http_status": 402, "response": {"error": "Insufficient credits"}}
    success = {"http_status": 200, "response": {"result": {"sku": "new"}}}
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call", side_effect=[denial, success]) as call,
    ):
        with pytest.raises(PermissionError, match="quota_or_payment"):
            run(home, profile, "cloudflare-json", target)
        assert not (target / "certificate.json").exists()
        run(home, profile, "cloudflare-json", target)
        run(home, profile, "cloudflare-json", target)
        assert call.call_count == 2
        manifest = read_json(target / "manifest.json")
        assert len(manifest["calls"]) == 1 and len(manifest["physical_calls"]) == 2
        current = compose(
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call") as call,
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.repair.fixture_check", return_value=[]) as qualify,
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 200, "response": {"data": {"json": {"sku": "ok"}}}},
        ) as call,
    ):
        run(home, profile, "firecrawl-json", tmp_path / "suffix")
    assert call.call_args.args[3].endswith("/" + cid + ".html")
    assert qualify.call_args.args[-1] == ".html"


@pytest.mark.parametrize("change", ["profile", "dataset"])
def test_repair_rejects_changed_inputs_before_certification(tmp_path, change):
    from harness.web_extract.pipeline import validate_dataset

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
            "harness.web_extract.repair.validate_dataset",
            side_effect=lambda path: validate_dataset(path, profile=home / "profile.json"),
        ),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call", side_effect=provider),
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call", side_effect=provider) as calls,
    ):
        with pytest.raises(ValueError, match="changed before dispatch"):
            run(home, profile, "cloudflare-json", target)
        assert calls.call_count == 1
    assert not (target / "certificate.json").exists()


def test_local_baseline_repair_uses_selected_dataset_home(tmp_path):
    home, _, profile, _ = parent_inputs(tmp_path, method="extruct")
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
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
    packet = read_packet(Path(manifest["packets"][0]["path"]))
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
    from harness.web_extract.review import run as run_audit

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
        patch("harness.web_extract.review.ReviewerSessions", Sessions),
        patch("harness.web_extract.review.ThreadPoolExecutor", Executor),
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.time.monotonic", side_effect=now),
        patch("harness.web_extract.pipeline.time.sleep", side_effect=sleep),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={
                "state": "response_received",
                "response": {"result": {"sku": "recovered"}},
                "http_status": 200,
            },
        ) as provider,
    ):
        run(home, profile, "cloudflare-json", tmp_path / "repaired")
    assert provider.call_count == 3


@pytest.mark.parametrize(
    "quote",
    [
        "“Model A”；“Price: 13.40”",
        "Model A || Price: 13.40",
        "Model A | Price: 13.40",
        "Model A / Price: 13.40",
        "Model A; Price: 13.40",
        "Model A\nPrice: 13.40",
        '["Model A", "Price: 13.40"]',
        "['Model A', 'Price: 13.40']",
        '"Model A"; "Price: 13.40"',
        '"Model A\\nCurrent offer\\nPrice: 13.40"',
        "'Model A\\nCurrent offer\\nPrice: 13.40'",
    ],
)
def test_separate_literal_citations_retain_all_fragments_and_judgments(tmp_path, quote):
    source = tmp_path / "source.txt"
    text = "Model A\nCurrent offer\nPrice: 13.40"
    source.write_text(text, encoding="utf-8")
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
    raw = review_payload(packet, quote)
    fixed = reconcile_citations(raw, packet)
    validate(fixed, packet)
    for section in ("source_fields", "reference"):
        for field, finding in fixed["cases"][0][section].items():
            assert finding["source_quote"] == text
            assert {k: v for k, v in finding.items() if k != "source_quote"} == {
                k: v for k, v in raw["cases"][0][section][field].items() if k != "source_quote"
            }
    assert raw["cases"][0]["source_fields"]["price"]["source_quote"] == quote
    invented = quote.replace("13.40", "999")
    rejected = reconcile_citations(review_payload(packet, invented), packet)
    with pytest.raises(ValueError, match="not literal"):
        validate(rejected, packet)


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
    assert (
        result["cases"][0]["source_fields"]["price"]["source_quote"]
        == "Product details. Actual price: 13.40"
    )


@pytest.mark.parametrize("nearby_repeat", [False, True])
def test_citation_uses_one_bounded_source_span_without_dropping_repeated_anchors(
    tmp_path, nearby_repeat
):
    source = tmp_path / "source.html"
    text = "Model A" + "x" * 5000 + ("Model A\n" if nearby_repeat else "") + "Price: 13.40"
    source.write_text(text, encoding="utf-8")
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
    quote = '["Model A", "Price: 13.40"]'
    raw = review_payload(packet, quote)
    raw["cases"][0]["reference"]["price"].update(status="incorrect", values=["Keep opinion"])
    result = reconcile_citations(raw, packet)
    if nearby_repeat:
        validate(result, packet)
        assert result["cases"][0]["reference"]["price"]["source_quote"] == "Model A\nPrice: 13.40"
        assert result["cases"][0]["reference"]["price"]["status"] == "incorrect"
        assert result["cases"][0]["reference"]["price"]["values"] == ["Keep opinion"]
    else:
        with pytest.raises(ValueError, match="not literal"):
            validate(result, packet)
        assert result == raw


def test_citation_list_never_executes_python_expressions(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Model A Price: 13.40", encoding="utf-8")
    sentinel = tmp_path / "must-not-exist"
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
    quote = f"[__import__('pathlib').Path({str(sentinel)!r}).write_text('untrusted')]"
    result = reconcile_citations(review_payload(packet, quote), packet)
    assert not sentinel.exists()
    with pytest.raises(ValueError, match="not literal"):
        validate(result, packet)


def test_repair_composition_accepts_verified_field_projection(tmp_path):
    home, source, profile, rows = parent_inputs(tmp_path)
    destination = tmp_path / "repaired"
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
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
        current = compose(home, source, projected, revision)
    assert current[0]["normalized_fields"] == {"sku": "offline projection"}
    assert current[-1]["normalized_fields"]["sku"] == "fixed"


@pytest.mark.parametrize("limit", [0, -1])
def test_review_rejects_nonpositive_limit_before_preparation_or_dispatch(tmp_path, limit):
    from harness.web_extract.cli import main
    from harness.web_extract.review import run as run_audit

    with patch("harness.web_extract.review.ReviewerSessions.start") as start:
        with pytest.raises(ValueError, match="positive integer"):
            run_audit(tmp_path / "absent", model="synthetic", limit=limit)
        start.assert_not_called()
    with (
        patch("harness.web_extract.review.prepare") as prepare_audit,
        patch("harness.web_extract.review.run") as dispatch,
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
    from harness.web_extract.review import assemble

    home, source, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "repaired"
    content = tmp_path / "current-content.json"
    initial = tmp_path / "initial"
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
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
            packet = read_packet(path)
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
    from harness.web_extract.review import run as run_audit

    unchanged = tmp_path / "unchanged-supplement"
    with (
        patch("harness.web_extract.review.ReviewerSessions.start") as dispatch,
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

    from harness.web_extract.review import run as run_audit

    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work)
    packet = read_packet(Path(manifest["packets"][0]["path"]))
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
        patch("harness.web_extract.review.ReviewerSessions.start", side_effect=start) as dispatch,
        patch("harness.web_extract.review.ReviewerSessions.communicate"),
    ):
        result = run_audit(work, model="synthetic", workers=1)
        assert result["needs_correction"] == [] and result["cases"] == 3
        assert dispatch.call_count == 1
        assert len(list((work / "invalid").glob("*.json"))) == 1
        assert next((work / "invalid").glob("*.json")).read_bytes() == original
        assert (work / "reviews/000.json").exists()
        run_audit(work, model="synthetic", workers=1)
        assert dispatch.call_count == 1


@pytest.mark.parametrize("supplemental_dispute", [False, True])
def test_supplemental_review_selects_only_changed_repaired_call(tmp_path, supplemental_dispute):
    from harness.web_extract.review import assemble

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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
    ):
        with patch("harness.web_extract.pipeline.provider_call", side_effect=[success, failure]):
            run(home, profile, "cloudflare-json", first)
        revisions = [
            {"path": str(first), "sha256": digest((first / "certificate.json").read_bytes())}
        ]
        save_json(content, {"evidence_runs": [{"run": str(source), "repair": revisions}]})
        manifest = prepare(home, content, initial)
        for entry in manifest["packets"]:
            packet = read_packet(Path(entry["path"]))
            save_json(initial / "reviews" / Path(entry["path"]).name, review_payload(packet))
        save_json(initial / "reviewer.json", {"model": "synthetic"})
        second_profile = tmp_path / "second-plan.json"
        create_plan(home, content, second_profile, {}, "https://frozen.example")
        second = tmp_path / "second-repair"
        with patch("harness.web_extract.pipeline.provider_call", return_value=success) as extract:
            run(home, second_profile, "cloudflare-json", second)
        assert extract.call_count == 1
        revisions.append(
            {"path": str(second), "sha256": digest((second / "certificate.json").read_bytes())}
        )
        save_json(content, {"evidence_runs": [{"run": str(source), "repair": revisions}]})
        supplement = tmp_path / "supplement"
        supplemental = prepare(home, content, supplement, prior=initial)
        assert supplemental["case_count"] == supplemental["result_count"] == 1
        packet = read_packet(Path(supplemental["packets"][0]["path"]))
        assert packet["cases"][0]["case_id"] == rows[-1]["case_id"]
        supplemental_opinion = review_payload(packet)
        if supplemental_dispute:
            supplemental_opinion["cases"][0]["reference"]["sku"]["status"] = "ambiguous"
        save_json(supplement / "reviews/000.json", supplemental_opinion)
        adjudication = tmp_path / "adjudication"
        save_json(
            adjudication / "manifest.json",
            {"bindings": {}, "packets": [], "case_count": 0, "result_count": 0},
        )
        if supplemental_dispute:
            with pytest.raises(ValueError, match="complete adjudication"):
                assemble(
                    home,
                    content,
                    initial,
                    supplement,
                    adjudication,
                    tmp_path / "unresolved.json",
                    profile=home / "profile.json",
                )
            from harness.web_extract.review import prepare_adjudication

            adjudication = tmp_path / "resolved-adjudication"
            resolved = prepare_adjudication(
                initial, adjudication, home / "profile.json", additional_priors=[supplement]
            )
            assert resolved["case_count"] == 1
            for entry in resolved["packets"]:
                case_packet = read_packet(Path(entry["path"]))
                save_json(
                    adjudication / "reviews" / Path(entry["path"]).name, review_payload(case_packet)
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
    from harness.web_extract.cli import main

    work = tmp_path / "adjudication"
    with (
        patch("harness.web_extract.review.prepare_adjudication") as prepare_audit,
        patch("harness.web_extract.review.run") as dispatch,
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

    from harness.web_extract.review import run as run_audit

    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work, batch_size=1)
    assert len(manifest["packets"]) == 3
    dispatched = []

    def start(command, **kwargs):
        raw = Path(command[command.index("--output-last-message") + 1])
        packet = read_packet(work / "packets" / raw.name)
        dispatched.append(raw.name)
        save_json(raw, review_payload(packet))
        return SimpleNamespace(returncode=0)

    with (
        patch("harness.web_extract.review.ReviewerSessions.start", side_effect=start),
        patch("harness.web_extract.review.ReviewerSessions.communicate"),
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
        patch("harness.web_extract.review.ReviewerSessions.start") as dispatch,
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
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
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.pipeline.provider_call") as dispatch,
        pytest.raises(ValueError, match="scope|journal"),
    ):
        run(home, profile, "cloudflare-json", target)
    dispatch.assert_not_called()
    assert not (target / "certificate.json").exists()


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5, "2", None])
@pytest.mark.parametrize("adjudication", [False, True])
def test_review_batch_size_is_validated_before_any_io(tmp_path, batch_size, adjudication):
    from harness.web_extract.review import prepare, prepare_adjudication

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
@pytest.mark.parametrize("finding_status", ["ambiguous", "absent"])
def test_complete_field_exclusion_assembles_and_builds_unavailable_metrics(
    tmp_path, field, finding_status
):
    from harness.web_extract.report import build, check
    from harness.web_extract.review import assemble, prepare, prepare_adjudication

    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work)
    for entry in manifest["packets"]:
        payload = review_payload(read_packet(Path(entry["path"])))
        for case in payload["cases"]:
            case["reference"][field]["status"] = finding_status
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
        payload = review_payload(read_packet(Path(entry["path"])))
        for case in payload["cases"]:
            case["reference"][field]["status"] = finding_status
        save_json(tmp_path / "adjudication/reviews" / Path(entry["path"]).name, payload)
    audit_path = tmp_path / "audit.json"
    if finding_status == "absent" and field != "price":
        with pytest.raises(ValueError, match="Absent nonempty reference"):
            assemble(
                home,
                home / "content.json",
                work,
                tmp_path / "supplemental",
                tmp_path / "adjudication",
                audit_path,
                profile=home / "profile.json",
            )
        assert not audit_path.exists()
        return
    audit = assemble(
        home,
        home / "content.json",
        work,
        tmp_path / "supplemental",
        tmp_path / "adjudication",
        audit_path,
        profile=home / "profile.json",
    )
    if finding_status == "absent":
        assert audit["unresolved"] == 0
        assert not audit["reference_changes"]
        return
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


def test_adjudication_cannot_be_mixed_with_another_valid_initial_audit(tmp_path, monkeypatch):
    from harness.web_extract.review import assemble, completed, prepare_adjudication

    home = create(tmp_path / "example")
    audits = []
    for name in ("audit-a", "audit-b"):
        work = tmp_path / name
        manifest = prepare(home, home / "content.json", work)
        for entry in manifest["packets"]:
            payload = review_payload(read_packet(Path(entry["path"])))
            for case in payload["cases"]:
                case["reference"]["sku"].update(status="ambiguous", reason=name)
            save_json(work / "reviews" / Path(entry["path"]).name, payload)
        save_json(work / "reviewer.json", {"model": "synthetic-test"})
        completed(work)
        audits.append(work)
    adjudication = tmp_path / "adjudication"
    manifest = prepare_adjudication(audits[0], adjudication, home / "profile.json")
    for entry in manifest["packets"]:
        payload = review_payload(read_packet(Path(entry["path"])))
        save_json(adjudication / "reviews" / Path(entry["path"]).name, payload)
    completed(adjudication)
    save_json(
        tmp_path / "supplemental/manifest.json",
        {"bindings": {}, "packets": [], "case_count": 0, "result_count": 0},
    )
    monkeypatch.chdir(tmp_path)
    args = (home, home / "content.json")
    assemble(
        *args,
        audits[0].relative_to(tmp_path),
        tmp_path / "supplemental",
        adjudication,
        tmp_path / "valid.json",
        profile=home / "profile.json",
    )
    with pytest.raises(ValueError, match="Adjudication belongs to a different original audit"):
        assemble(
            *args,
            audits[1],
            tmp_path / "supplemental",
            adjudication,
            tmp_path / "mixed.json",
            profile=home / "profile.json",
        )
    assert not (tmp_path / "mixed.json").exists()


@pytest.mark.parametrize("boundary", ["root", "sources-directory", "outline", "log"])
def test_audit_rejects_linked_outputs_without_writing_targets_or_dispatch(tmp_path, boundary):
    from harness.web_extract.review import run as run_audit

    home = create(tmp_path / "example")
    work = tmp_path / "review"
    target = tmp_path / "untouched-target"
    if boundary == "root":
        work.symlink_to(target, target_is_directory=True)
    elif boundary == "sources-directory":
        work.mkdir()
        (work / "sources").symlink_to(target, target_is_directory=True)
    elif boundary == "outline":
        (work / "sources").mkdir(parents=True)
        (work / "sources" / ("1" * 64 + ".txt")).symlink_to(target)
    else:
        prepare(home, home / "content.json", work)
        (work / "logs").mkdir()
        (work / "logs/000.log").symlink_to(target)
    with patch("harness.web_extract.review.ReviewerSessions.start") as dispatch:
        with pytest.raises(ValueError, match="[Ss]ymlink|Unsafe|outside"):
            if boundary == "log":
                run_audit(work, model="synthetic")
            else:
                prepare(home, home / "content.json", work)
    dispatch.assert_not_called()
    assert not target.exists()


def test_repair_rejects_dangling_lock_before_creating_target_or_dispatch(tmp_path):
    home, _, profile, _ = parent_inputs(tmp_path)
    destination = tmp_path / "repair"
    destination.mkdir()
    target = tmp_path / "lock-target"
    (destination / "execution.lock").symlink_to(target)
    with patch("harness.web_extract.pipeline.provider_call") as dispatch:
        with pytest.raises(ValueError, match="[Ss]ymlink|Unsafe"):
            run(home, profile, "cloudflare-json", destination)
    dispatch.assert_not_called()
    assert not target.exists()


def test_review_bundle_can_move_without_rewriting_hash_bound_files(tmp_path):
    import shutil

    from harness.web_extract.review import completed

    original = tmp_path / "original"
    original.mkdir()
    home = create(original / "example")
    work = original / "review"
    manifest = prepare(home, home / "content.json", work)
    stored = read_json(work / "manifest.json")
    assert all(not Path(name).is_absolute() for name in stored["bindings"])
    for entry in manifest["packets"]:
        packet = read_packet(Path(entry["path"]))
        save_json(work / "reviews" / Path(entry["path"]).name, review_payload(packet))
    expected = completed(work)[2]
    before = {
        p.relative_to(original).as_posix(): digest(p.read_bytes())
        for p in original.rglob("*")
        if p.is_file()
    }
    restored = tmp_path / "restored"
    shutil.move(original, restored)
    assert completed(restored / "review")[2] == expected
    assert all(digest((restored / name).read_bytes()) == sha for name, sha in before.items())


@pytest.mark.parametrize("evidence", ["journal", "inflight", "call"])
def test_repair_cannot_adopt_dispatch_without_its_original_contract(tmp_path, evidence):
    home, _, profile, _ = parent_inputs(tmp_path)
    target = tmp_path / "unbound"
    path = {
        "journal": "calls.journal.jsonl",
        "inflight": "inflight.json",
        "call": "calls/attempt.json",
    }[evidence]
    save_json(target / path, {})
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.pipeline.provider_call") as dispatch,
    ):
        with pytest.raises(ValueError, match="contract is absent"):
            run(home, profile, "cloudflare-json", target)
    dispatch.assert_not_called()
    assert not (target / "contract.json").exists()


def test_adjudication_rules_follow_a_relocated_bundle(tmp_path):
    import shutil

    from harness.web_extract.review import save_manifest, save_packet

    root = tmp_path / "old"
    root.mkdir()
    rules = root / "rules.txt"
    rules.write_text("frozen rules", encoding="utf-8")
    for name in ["input.html", "outline.txt"]:
        (root / name).write_text("source", encoding="utf-8")
    work = root / "audit"
    packet = work / "packets/000.json"
    save_packet(
        packet,
        {
            "cases": [
                {
                    "source_html": str(root / "input.html"),
                    "source_outline": str(root / "outline.txt"),
                    "benchmark_rules": str(rules),
                }
            ]
        },
        root,
    )
    save_manifest(
        work,
        {
            "bindings": {str(rules): digest(rules.read_bytes())},
            "packets": [{"path": str(packet), "sha256": digest(packet.read_bytes())}],
        },
        root,
    )
    moved = tmp_path / "moved"
    shutil.move(root, moved)
    case = read_packet(moved / "audit/packets/000.json")["cases"][0]
    assert Path(case["benchmark_rules"]) == moved / "rules.txt"
    assert Path(case["benchmark_rules"]).read_text(encoding="utf-8") == "frozen rules"


def test_original_access_retry_usage_survives_a_later_repair(tmp_path):
    import json

    from harness.web_extract.repair import load_selection

    home, source, _, rows = parent_inputs(tmp_path)
    contract = read_json(source / "contract.json")
    contract["access_retry_limit"] = 1
    save_json(source / "contract.json", contract)
    manifest = read_json(source / "manifest.json")
    winner_path = source / manifest["calls"][0]["path"]
    winner = read_json(winner_path)
    denied = {
        **winner,
        "physical_attempt": 1,
        "http_status": 402,
        "response": {"error": "Insufficient credits", "usage": 7},
        "normalized_fields": None,
        "compatibility": "no_structured_output",
        "elapsed_seconds": 2,
    }
    denied_path = source / "calls/denied.json"
    save_json(denied_path, denied)
    winner.update(physical_attempt=2, retry_execution_seconds=2)
    save_json(winner_path, winner)
    manifest["calls"][0]["sha256"] = digest(winner_path.read_bytes())
    manifest["physical_calls"] = [
        {"path": "calls/denied.json", "sha256": digest(denied_path.read_bytes())},
        *manifest["calls"],
    ]
    manifest["contract_sha256"] = digest((source / "contract.json").read_bytes())
    save_json(source / "manifest.json", manifest)
    (source / "calls.journal.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in manifest["physical_calls"]), encoding="utf-8"
    )
    save_json(
        source / "certificate.json",
        {"manifest_sha256": digest((source / "manifest.json").read_bytes())},
    )
    content = tmp_path / "selected.json"
    save_json(content, {"evidence_runs": [{"run": str(source)}]})
    profile = tmp_path / "retry-aware-plan.json"
    create_plan(home, content, profile, {}, None)
    repaired = tmp_path / "repaired"
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 200, "response": {"result": {"sku": "new"}}},
        ),
    ):
        run(home, profile, "cloudflare-json", repaired)
    _, selected = load_selection(
        home,
        {
            "run": str(source),
            "repair": [
                {
                    "path": str(repaired),
                    "sha256": digest((repaired / "certificate.json").read_bytes()),
                }
            ],
        },
    )
    assert len(selected[0]["physical_attempts"]) == 2
    assert selected[0]["physical_attempts"][0]["response"]["usage"] == 7
    assert sum(len(row.get("physical_attempts", [row])) for row in selected) == 5
    # Recording a one-call history must not change the superseded execution.
    original = next(
        read_json(source / entry["path"])
        for entry in manifest["calls"]
        if read_json(source / entry["path"])["case_id"] == selected[-1]["case_id"]
    )
    assert selected[-1]["superseded_result"] == original


@pytest.mark.parametrize("changed", ["html", "reference"])
def test_reviews_of_another_dataset_cannot_amend_current_scores(tmp_path, changed):
    import shutil

    from harness.web_extract.review import assemble

    home = create(tmp_path / "report")
    alternate = tmp_path / "alternate"
    shutil.copytree(home, alternate)
    if changed == "html":
        source = next((alternate / "dataset/html").glob("*.html"))
        source.write_text(
            source.read_text(encoding="utf-8") + "<p>other record</p>", encoding="utf-8"
        )
    else:
        path = alternate / "dataset/dataset/ground-truth.json"
        truth = read_json(path)
        truth[next(iter(truth))]["sku"] = ["another dataset value"]
        save_json(path, truth)
    initial = tmp_path / "review"
    manifest = prepare(alternate, alternate / "content.json", initial)
    for entry in manifest["packets"]:
        save_json(
            initial / "reviews" / Path(entry["path"]).name,
            review_payload(read_packet(Path(entry["path"]))),
        )
    save_json(initial / "reviewer.json", {"model": "synthetic-test"})
    for name in ["supplemental", "adjudication"]:
        save_json(
            tmp_path / name / "manifest.json",
            {"bindings": {}, "packets": [], "case_count": 0, "result_count": 0},
        )
    with pytest.raises(ValueError, match="Reviewed dataset differs"):
        assemble(
            home,
            home / "content.json",
            initial,
            tmp_path / "supplemental",
            tmp_path / "adjudication",
            tmp_path / "audit.json",
            profile=home / "profile.json",
        )
    assert not (tmp_path / "audit.json").exists()


@pytest.mark.parametrize("key", ["reason", "source_locator", "source_quote"])
def test_product_review_rejects_whitespace_evidence(tmp_path, key):
    home = create(tmp_path / "example")
    work = tmp_path / "review"
    manifest = prepare(home, home / "content.json", work)
    packet = read_packet(Path(manifest["packets"][0]["path"]))
    payload = review_payload(packet)
    payload["cases"][0]["reference"]["sku"][key] = " \n\t"
    with pytest.raises(ValueError):
        validate(payload, packet)


@pytest.mark.parametrize("legacy", [False, True])
def test_managed_snapshot_repair_collects_verifies_composes_and_resumes(tmp_path, legacy):
    from harness.web_extract import snapshots

    home, source, _, rows = parent_inputs(tmp_path)
    base = "https://frozen.example"
    with patch("harness.web_extract.snapshots.validate_dataset"):
        version, cases = snapshots.inventory(home)
    receipt = {
        "hosting_provider": "cloudflare",
        "mode": "dataset_manifest",
        "manifest_url": base + "/snapshots.json",
        "manifest_sha256": "a" * 64,
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "fixture_base": base,
    }
    contract = read_json(source / "contract.json")
    contract.update(snapshot_manifest=receipt, fixture_base=base, fixture_suffix="")
    save_json(source / "contract.json", contract)
    manifest = read_json(source / "manifest.json")
    manifest["contract_sha256"] = digest((source / "contract.json").read_bytes())
    save_json(source / "manifest.json", manifest)
    save_json(
        source / "certificate.json",
        {"manifest_sha256": digest((source / "manifest.json").read_bytes())},
    )
    plan = tmp_path / "managed-plan.json"
    create_plan(
        home, tmp_path / "content.json", plan, {"cloudflare-json": {"input_route": "url"}}, base
    )
    target = tmp_path / "managed-repair"
    response = {
        "state": "response_received",
        "http_status": 200,
        "response": {"result": {"sku": "recovered"}},
    }
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch("harness.web_extract.snapshots.validate_dataset"),
        patch("harness.web_extract.snapshots.check_manifest", return_value=receipt),
        patch("harness.web_extract.pipeline.provider_call", return_value=response) as provider,
    ):
        run(home, plan, "cloudflare-json", target)
        run(home, plan, "cloudflare-json", target)
        if legacy:
            # Archived managed receipts predate the explicit hosting-policy field.
            archived = read_json(target / "contract.json")
            archived["participant"]["snapshot_manifest"].pop("hosting_provider")
            save_json(target / "contract.json", archived)
            manifest = read_json(target / "manifest.json")
            for name in ("fixture-before.json", "fixture-after.json"):
                receipt_rows = read_json(target / name)
                receipt_rows[0]["snapshot_manifest"].pop("hosting_provider")
                save_json(target / name, receipt_rows)
                manifest["fixtures"][name] = digest((target / name).read_bytes())
            manifest["contract_sha256"] = digest((target / "contract.json").read_bytes())
            save_json(target / "manifest.json", manifest)
            certificate = read_json(target / "certificate.json")
            certificate["manifest_sha256"] = digest((target / "manifest.json").read_bytes())
            save_json(target / "certificate.json", certificate)
        revision = {
            "path": str(target),
            "sha256": digest((target / "certificate.json").read_bytes()),
        }
        composed = compose(home, source, deepcopy(rows), revision)
        assert provider.call_count == 1
    assert composed[-1]["normalized_fields"]["sku"] == "recovered"
    assert read_json(target / "fixture-before.json")[0]["case_id"] == "dataset-snapshot"


@pytest.mark.parametrize(
    "source,quote,accepted",
    [
        (r"<p>C:\SKU</p><p>Price</p>", r'"C:\SKU"; "Price"', True),
        (r"<p>C:\SKU</p><p>Price</p>", r'"C:\SKU"; "Invented"', False),
    ],
)
def test_literal_backslashes_in_multiple_citations_require_every_fragment(source, quote, accepted):
    from harness.web_extract.review import citation_fragments, literal_citation_span

    span = literal_citation_span(source, citation_fragments(quote))
    assert (span is not None) is accepted
    if accepted:
        assert r"C:\SKU" in span and "Price" in span


def test_malformed_literal_citation_does_not_crash_recovery():
    from harness.web_extract.review import citation_fragments

    assert citation_fragments('{["a"]: 1}') == []


def test_empty_repair_options_inherit_the_parent_url_route(tmp_path):
    home, source, _, _ = parent_inputs(
        tmp_path, native_options={"input_route": "url", "setJavaScriptEnabled": False}
    )
    contract = read_json(source / "contract.json")
    output = tmp_path / "inherited-plan.json"
    create_plan(home, tmp_path / "content.json", output, {}, "https://frozen.example")
    participant = read_json(output)["participants"]["cloudflare-json"]
    assert participant["native_options"] == contract["native_options"]


@pytest.mark.parametrize("value", ["parent", "runs/parent", "absolute"])
def test_repair_and_review_selection_accept_explicit_or_bare_run_names(tmp_path, value):
    from harness.web_extract.repair import load_selection

    home, source, _, _ = parent_inputs(tmp_path)
    selected, rows = load_selection(home, {"run": str(source) if value == "absolute" else value})
    assert selected == source
    assert len(rows) == 3


def test_adjudication_uses_the_same_dataset_root_for_different_audit_depths(tmp_path):
    from harness.web_extract.review import manifest_root, prepare_adjudication, read_manifest

    home = create(tmp_path / "example")
    priors = [tmp_path / "initial", tmp_path / "repairs/deeper/supplemental"]
    for prior in priors:
        manifest = prepare(home, home / "content.json", prior)
        for entry in manifest["packets"]:
            packet = read_packet(Path(entry["path"]))
            payload = review_payload(packet)
            payload["cases"][0]["reference"]["sku"]["status"] = "ambiguous"
            save_json(prior / "reviews" / Path(entry["path"]).name, payload)
    rules = tmp_path / "rules.txt"
    rules.write_text("Resolve references from source text.", encoding="utf-8")
    output = tmp_path / "final-adjudication"
    result = prepare_adjudication(priors[0], output, rules, additional_priors=[priors[1]])
    assert manifest_root(output, read_manifest(output)) == home
    assert all(
        Path(case["source_html"]).is_file()
        for entry in result["packets"]
        for case in read_packet(Path(entry["path"]))["cases"]
    )


@pytest.mark.parametrize("instructions", ["Keep the parent record wording.", ""])
def test_repair_freezes_parent_instructions_and_rejects_a_changed_plan(tmp_path, instructions):
    from harness.web_extract.repair import verify_parent

    home, source, _, _ = parent_inputs(tmp_path)
    contract = read_json(source / "contract.json")
    contract["extraction_instructions"] = instructions
    save_json(source / "contract.json", contract)
    manifest = read_json(source / "manifest.json")
    manifest["contract_sha256"] = digest((source / "contract.json").read_bytes())
    save_json(source / "manifest.json", manifest)
    save_json(
        source / "certificate.json",
        {"manifest_sha256": digest((source / "manifest.json").read_bytes())},
    )
    profile = tmp_path / "instruction-plan.json"
    create_plan(home, tmp_path / "content.json", profile, {}, None)
    participant = read_json(profile)["participants"]["cloudflare-json"]
    assert participant["extraction_instructions"] == instructions
    verify_parent(home, participant)
    participant["extraction_instructions"] = "Changed meaning"
    with pytest.raises(ValueError, match="instructions differ"):
        verify_parent(home, participant)


def test_historical_record_instruction_delivery_is_not_silently_upgraded(tmp_path):
    home, source, _, rows = parent_inputs(tmp_path)
    output = tmp_path / "not-created.json"
    with (
        patch("harness.web_extract.repair.load_selection", return_value=(source, rows, {})),
        patch(
            "harness.web_extract.repair.task_bindings",
            return_value={"task_adapter_sha256": "a" * 64},
        ),
    ):
        with pytest.raises(ValueError, match="Historical instruction delivery"):
            create_plan(home, tmp_path / "content.json", output, {}, None)
    assert not output.exists()


@pytest.mark.parametrize("distinct_run", [False, True])
def test_repair_plan_rejects_two_parents_for_the_same_method(tmp_path, distinct_run):
    import shutil

    home, source, _, _ = parent_inputs(tmp_path)
    second = home / "runs/second"
    if distinct_run:
        shutil.copytree(source, second)
    content = tmp_path / "duplicate-selection.json"
    save_json(
        content,
        {"evidence_runs": [{"run": str(source)}, {"run": str(second if distinct_run else source)}]},
    )
    output = tmp_path / "not-created.json"
    with pytest.raises(ValueError, match="duplicate methods"):
        create_plan(home, content, output, {}, None)
    assert not output.exists()


def test_repair_plan_rejects_a_parent_with_multiple_methods(tmp_path):
    home, source, _, rows = parent_inputs(tmp_path)
    contract = read_json(source / "contract.json")
    contract["methods"] = ["cloudflare-json", "zyte-product"]
    save_json(source / "contract.json", contract)
    manifest = read_json(source / "manifest.json")
    for row in rows:
        second = {
            **row,
            "method": "zyte-product",
            "response": {"product": row["normalized_fields"] or {}},
        }
        path = source / "calls" / ("zyte-product-" + row["case_id"] + ".json")
        save_json(path, second)
        manifest["calls"].append(
            {"path": "calls/" + path.name, "sha256": digest(path.read_bytes())}
        )
    manifest["contract_sha256"] = digest((source / "contract.json").read_bytes())
    save_json(source / "manifest.json", manifest)
    save_json(
        source / "certificate.json",
        {"manifest_sha256": digest((source / "manifest.json").read_bytes())},
    )
    output = tmp_path / "not-created.json"
    with pytest.raises(ValueError, match="exactly one method"):
        create_plan(home, tmp_path / "content.json", output, {}, None)
    assert not output.exists()


@pytest.mark.parametrize("url_input", [False, True])
def test_chained_repair_inherits_current_execution_settings(tmp_path, url_input):
    home, source, first, rows = parent_inputs(
        tmp_path, native_options={"setJavaScriptEnabled": True}
    )
    plan = read_json(first)
    participant = plan["participants"]["cloudflare-json"]
    participant["native_options"] = {"setJavaScriptEnabled": False}
    if url_input:
        participant["native_options"]["input_route"] = "url"
        participant["fixture_suffix"] = ""
    save_json(first, plan)
    destination = tmp_path / "first-repair"
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.repair.fixture_check",
            return_value=[
                {
                    "case_id": rows[-1]["case_id"],
                    "url": plan["fixture_base"]
                    + "/"
                    + rows[-1]["case_id"]
                    + participant["fixture_suffix"],
                    "sha256": rows[-1]["input_sha256"],
                }
            ],
        ),
        patch("harness.web_extract.pipeline.qualify_fixture", return_value={}),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 500, "response": {}},
        ) as call,
    ):
        run(home, first, "cloudflare-json", destination)
        content = tmp_path / "chain.json"
        save_json(
            content,
            {
                "evidence_runs": [
                    {
                        "run": str(source),
                        "repair": [
                            {
                                "path": str(destination),
                                "sha256": digest((destination / "certificate.json").read_bytes()),
                            }
                        ],
                    }
                ]
            },
        )
        second = tmp_path / "second-plan.json"
        updated = create_plan(home, content, second, {}, None)
        assert (
            updated["participants"]["cloudflare-json"]["native_options"]
            == participant["native_options"]
        )
        if url_input:
            assert updated["fixture_base"] == plan["fixture_base"]
            assert updated["participants"]["cloudflare-json"]["fixture_suffix"] == ""
        run(home, second, "cloudflare-json", tmp_path / "second-repair")
        assert call.call_count == 2
        assert call.call_args.kwargs["native_options"] == participant["native_options"]


def test_chained_repair_rejects_mixed_current_settings_before_writing(tmp_path):
    home, source, first, rows = parent_inputs(
        tmp_path, success_count=1, native_options={"setJavaScriptEnabled": True}
    )
    plan = read_json(first)
    participant = plan["participants"]["cloudflare-json"]
    participant["case_ids"] = [rows[-1]["case_id"]]
    participant["native_options"] = {"setJavaScriptEnabled": False}
    save_json(first, plan)
    destination = tmp_path / "first-repair"
    with (
        patch("harness.web_extract.repair.validate_dataset"),
        patch("harness.web_extract.repair.load_runtime_env"),
        patch(
            "harness.web_extract.pipeline.provider_call",
            return_value={"http_status": 500, "response": {}},
        ),
    ):
        run(home, first, "cloudflare-json", destination)
    content = tmp_path / "chain.json"
    save_json(
        content,
        {
            "evidence_runs": [
                {
                    "run": str(source),
                    "repair": [
                        {
                            "path": str(destination),
                            "sha256": digest((destination / "certificate.json").read_bytes()),
                        }
                    ],
                }
            ]
        },
    )
    second = tmp_path / "second-plan.json"
    with pytest.raises(ValueError, match="different execution settings"):
        create_plan(home, content, second, {}, None)
    assert not second.exists()
