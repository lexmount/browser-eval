"""Full planned populations, checkpoint evidence and certified quality presentation."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import build, check, cohort_metrics


def test_progress_includes_unexecuted_cases_without_inventing_calls(tmp_path):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    assert manifest["observations"] == 4
    assert len(manifest["planned_methods"]) == 2
    inventory = BeautifulSoup(
        (output / "zh/results.html").read_text(encoding="utf-8"), "html.parser"
    )
    assert len(inventory.select("tr[data-status]")) == 6
    assert len(inventory.select('tr[data-status="pending"]')) == 2
    for pending in inventory.select('tr[data-status="pending"]'):
        assert pending.select_one("code").get_text() in pending["data-search"]
        assert "https://" in pending["data-search"]
    home = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    assert all(
        c.get_text(strip=True) == "—"
        for c in home.select("[data-core-metrics] tbody td:nth-child(n+6)")
    )
    assert check(output)["state"] == "full-cohort-progress"
    # Evidence is grouped by method, loaded on demand, rather than copied per detail.
    assert len(list((output / "public-evidence").glob("*.json"))) == 2
    assert "data-key=" in next((output / "zh").glob("result-*.html")).read_text(encoding="utf-8")


def test_checkpoint_rejects_modified_calls_and_missing_participants(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    content = read_json(home / "content.json")
    source = home / "runs/synthetic-success"
    call = next((source / "calls").glob("*.json"))
    original = call.read_bytes()
    call.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Checkpoint call changed"):
        build(
            tmp_path / "tampered", home / "content.json", home=home, profile=home / "profile.json"
        )
    call.write_bytes(original)
    content["evidence_runs"].pop()
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="missing a planned method"):
        build(tmp_path / "missing", home / "content.json", home=home, profile=home / "profile.json")


@pytest.mark.parametrize("completed", [False, True])
def test_current_collector_hash_only_run_builds_progress_and_certified_reports(
    tmp_path, monkeypatch, completed
):
    from harness.web_extract import certification, pipeline

    home = create(tmp_path / "fixture", mode="full-cohort-progress")
    profile = home / "profile.json"
    model = read_json(profile)
    model["dataset"]["files"]["evaluate.py"] = digest((home / "dataset/evaluate.py").read_bytes())
    save_json(profile, model)
    validate = pipeline.validate_dataset
    scorer = pipeline.official_scorer
    monkeypatch.setattr(pipeline, "selected_profile", lambda _: profile)
    monkeypatch.setattr(certification, "selected_profile", lambda _: profile)
    monkeypatch.setattr(pipeline, "validate_dataset", lambda h, p=None: validate(h, p or profile))
    monkeypatch.setattr(certification, "validate_dataset", lambda h: validate(h, profile))
    monkeypatch.setattr(pipeline, "official_scorer", lambda h: scorer(h, profile))
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "fixture")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fixture")
    calls = []

    def respond(*args, **kwargs):
        calls.append(args)
        if not completed and len(calls) == 2:
            raise KeyboardInterrupt
        return {
            "state": "response_received",
            "http_status": 200,
            "elapsed_seconds": 0.01,
            "response": {"result": {"price": "4", "sku": "demo", "availability": "InStock"}},
        }

    monkeypatch.setattr(pipeline, "provider_call", respond)
    plan = home / "fresh-plan.json"
    certification.create_plan(home, ["cloudflare-json"], plan)
    source = home / "runs/fresh"
    if completed:
        certification.run(home, plan, "cloudflare-json", source)
    else:
        with pytest.raises(KeyboardInterrupt):
            certification.run(home, plan, "cloudflare-json", source)
    assert not (source / "execution/pipeline.py").exists()
    content = read_json(home / "content.json")
    content["cohort_plan"] = {"path": str(plan), "sha256": digest(plan.read_bytes())}
    content["evidence_runs"] = [
        {"run": str(source), "plan": str(plan), "plan_sha256": digest(plan.read_bytes())}
    ]
    content["prices"] = {"cloudflare-json": content["prices"]["synthetic-success"]}
    save_json(home / "content.json", content)
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=profile)
    manifest = read_json(output / "report-manifest.json")
    assert manifest["observations"] == (3 if completed else 1)
    assert manifest["source_runs"][0]["certified"] is completed


@pytest.mark.parametrize(
    "mutation", ["duplicate", "skipped_attempt", "after_success", "quality_retry"]
)
def test_checkpoint_report_rejects_invalid_physical_history(tmp_path, mutation):
    import json

    home = create(tmp_path / "fixture", mode="full-cohort-progress")
    source = home / "runs/synthetic-success"
    journal = source / "calls.journal.jsonl"
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    if mutation == "duplicate":
        entries.append(entries[0])
    else:
        first = read_json(source / entries[0]["path"])
        if mutation == "quality_retry":
            first.update(http_status=500, compatibility="native_unstructured")
            save_json(source / entries[0]["path"], first)
            entries[0]["sha256"] = digest((source / entries[0]["path"]).read_bytes())
        retried = {
            **first,
            "physical_attempt": 3 if mutation == "skipped_attempt" else 2,
            "retry_execution_seconds": first.get("elapsed_seconds", 0),
        }
        path = source / "calls/retry.json"
        save_json(path, retried)
        entries.append({"path": "calls/retry.json", "sha256": digest(path.read_bytes())})
        contract = read_json(source / "contract.json")
        contract["access_retry_limit"] = 1
        save_json(source / "contract.json", contract)
    journal.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    output = tmp_path / "invalid-report"
    with pytest.raises(ValueError, match="physical|Physical|Retry|retry"):
        build(output, home / "content.json", home=home, profile=home / "profile.json")
    assert not output.exists()


@pytest.mark.parametrize("methods", [[], ["different-method"]])
def test_full_cohort_cannot_filter_away_a_declared_participant(tmp_path, methods):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    content = read_json(home / "content.json")
    content["evidence_runs"][0]["methods"] = methods
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="filters must retain"):
        build(
            tmp_path / "filtered", home / "content.json", home=home, profile=home / "profile.json"
        )


def test_public_evidence_requires_complete_projection_receipts(tmp_path):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    manifest["public_evidence_views"].pop()
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="receipts are incomplete"):
        check(output)


def test_contract_url_credentials_are_omitted_from_public_metadata(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    source = home / "runs/synthetic-success"
    contract = read_json(source / "contract.json")
    contract["fixture_base"] = (
        "https://private-user:private-pass@frozen.example/?api_key=private-token"
    )
    save_json(source / "contract.json", contract)
    output = tmp_path / "projected"
    build(output, home / "content.json", home=home, profile=home / "profile.json")
    metadata = (output / "report-manifest.json").read_text(encoding="utf-8")
    assert "private-pass" not in metadata
    assert "private-token" not in metadata
    manifest = read_json(output / "report-manifest.json")
    manifest["source_runs"][0]["contract"]["fixture_base"] = contract["fixture_base"]
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="metadata.*privacy"):
        check(output)


@pytest.mark.parametrize("mode", ["compatibility", "full-cohort-progress"])
def test_cost_prices_and_latency_cannot_omit_a_compared_participant(tmp_path, mode):
    home = create(tmp_path / "first", mode=mode)
    content = read_json(home / "content.json")
    content["prices"].pop("synthetic-blocked")
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="Every participant requires sourced cost"):
        build(
            tmp_path / "missing-price",
            home / "content.json",
            home=home,
            profile=home / "profile.json",
        )


def test_multiple_certified_plans_require_explicit_report_composition_binding(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    content = read_json(home / "content.json")
    selection = content["evidence_runs"][0]
    source = home / "runs" / selection["run"]
    plan = read_json(Path(selection["plan"]))
    plan["participants"][selection["run"]]["minimum_call_interval_seconds"] = 13
    variant = home / "variant-plan.json"
    save_json(variant, plan)
    sha = digest(variant.read_bytes())
    selection.update(plan=str(variant), plan_sha256=sha)
    precall = read_json(source / "certification-precall.json")
    precall["profile_sha256"] = sha
    save_json(source / "certification-precall.json", precall)
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="not bound by the report composition"):
        build(tmp_path / "unbound", home / "content.json", home=home, profile=home / "profile.json")
    content["source_cohorts"] = {selection["run"]: sha}
    save_json(home / "content.json", content)
    build(tmp_path / "bound", home / "content.json", home=home, profile=home / "profile.json")
    assert check(tmp_path / "bound")["observations"] == 4


def test_progress_release_check_rejects_changed_counts_even_with_new_file_hash(tmp_path):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    page = output / "zh/index.html"
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    soup.select_one("[data-core-metrics] tbody tr td:nth-child(3)").string = "99"
    page.write_text(str(soup), encoding="utf-8")
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/index.html"] = digest(page.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="progress count differs"):
        check(output)


def test_quality_highlights_all_raw_leaders_and_leaves_missing_values_neutral():
    def metrics(n):
        return {f: {"f1": n} for f in ("price", "sku", "availability", "OutOfStock")}

    bindings = [{"certified": True, "scores": {"a": metrics(0.81), "b": metrics(0.80)}}]
    body, _ = cohort_metrics(["a", "b", "pending"], [], bindings, 140, "zh")
    soup = check_presentation(body)
    assert len(soup.select(".comparison-winner")) == 4
    assert soup.select("tbody tr")[-1].select("td")[-1].get_text() == "—"
    bindings[0]["scores"]["b"] = metrics(0.81)
    body, _ = cohort_metrics(["a", "b"], [], bindings, 140, "zh")
    assert len(check_presentation(body).select(".comparison-winner")) == 8


def test_requested_english_requires_matching_authored_translation(tmp_path):
    home = create(tmp_path / "first")
    content = read_json(home / "content.json")
    content["pages"]["index"]["body"].pop("en")
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="English content is missing"):
        build(
            tmp_path / "english",
            home / "content.json",
            home=home,
            profile=home / "profile.json",
            language="en",
        )


def test_latency_uses_one_population_and_separates_missing_time_and_retries():
    rows = [
        {"method": "a", "elapsed_seconds": 1, "compatibility": "structured_output"},
        {"method": "a", "elapsed_seconds": 2, "compatibility": "structured_output"},
        {"method": "a", "elapsed_seconds": 10, "retry_execution_seconds": 0.5},
        {"method": "a", "state": "call_error"},
        {"method": "b", "elapsed_seconds": 100, "compatibility": "structured_output"},
    ]
    _, html = cohort_metrics(["a", "b"], rows, [], 140, "en")
    soup = check_presentation(html)
    cells = [[c.get_text(strip=True) for c in r.select("td")] for r in soup.select("tbody tr")]
    assert cells[0][2:] == ["3", "1", "0.50", "4.333", "2.000", "10.000"]
    assert cells[1][2:] == ["2", "0", "0.00", "1.500", "1.500", "2.000"]


def test_usage_retains_denied_physical_attempt_without_inventing_a_charge():
    from harness.web_extract.report import usage_table

    failed = {"response": {"success": False}, "http_status": 402}
    paid = {"response": {"data": {"metadata": {"creditsUsed": 5}}}}
    rows = [{"method": "native", "physical_attempts": [failed, paid]}]
    soup = BeautifulSoup(usage_table(["native"], rows, "en"), "html.parser")
    assert [c.get_text(strip=True) for c in soup.select("tbody td")] == [
        "native",
        "2",
        "1",
        "5 credits",
    ]


def test_missing_historical_retry_timer_is_never_presented_as_zero_cost():
    rows = [
        {
            "method": "a",
            "elapsed_seconds": 2,
            "retry_execution_seconds": 0,
            "retry_missing_timers": 1,
            "compatibility": "structured_output",
        }
    ]
    _, html = cohort_metrics(["a"], rows, [], 1, "en")
    soup = BeautifulSoup(html, "html.parser")
    assert "additional attempts untimed" in soup.select("tbody tr")[0].select("td")[4].get_text()
    assert soup.select("tbody tr")[0].select("td")[5].get_text() == "2.000"


@pytest.mark.parametrize(
    "selector", ["[data-latency-table] td:nth-child(6)", "[data-usage-table] td:nth-child(2)"]
)
def test_progress_check_rejects_timing_or_usage_value_changes(tmp_path, selector):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    page = output / "zh/protocol.html"
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    cell = soup.select_one(selector)
    marker = cell.select_one(".comparison-winner")
    (marker if marker is not None else cell).string = "9999"
    page.write_text(str(soup), encoding="utf-8")
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/protocol.html"] = digest(page.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="Timing or usage"):
        check(output)


def test_external_participant_labels_are_used_without_changing_evidence_identity(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    content = read_json(home / "content.json")
    content["method_labels"] = {"synthetic-success": "Source A", "synthetic-blocked": "Source B"}
    save_json(home / "content.json", content)
    output = tmp_path / "labeled"
    build(output, home / "content.json", home=home, profile=home / "profile.json")
    check(output)
    soup = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    assert [
        r.select_one("td").get_text(strip=True) for r in soup.select("[data-core-metrics] tbody tr")
    ] == ["Source A", "Source B"]


@pytest.mark.parametrize("change", ["cost", "filter", "field", "comparison"])
def test_release_rejects_missing_operating_metrics_and_broken_drilldown(tmp_path, change):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    cid = "1" * 64
    route, selector = {
        "cost": ("index.html", "[data-operating-metrics]"),
        "filter": ("results.html", '[data-result-filter="field"]'),
        "field": (f"result-synthetic-success-{cid}.html", "[data-field-diagnostics]"),
        "comparison": (f"case-{cid}.html", "[data-case-comparison] tbody tr"),
    }[change]
    path = output / "zh" / route
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    soup.select_one(selector).decompose()
    path.write_text(str(soup), encoding="utf-8")
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/" + route] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="Home latency|filters|Field diagnostics|same-case"):
        check(output)


def test_package_checks_release_and_includes_only_the_frozen_public_tree(tmp_path):
    import zipfile
    from unittest.mock import patch

    from harness.web_extract.report import package

    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    (tmp_path / "private-secret.txt").write_text("Never package siblings", encoding="utf-8")
    receipt = tmp_path / "visual.json"
    receipt.write_text("{}", encoding="utf-8")
    with patch("harness.web_extract.report.check") as release_check:
        result = package(output, tmp_path / "public.zip", visual_receipt=receipt)
    release_check.assert_called_once_with(output, release=True, visual_receipt=receipt)
    with zipfile.ZipFile(tmp_path / "public.zip") as bundle:
        assert set(bundle.namelist()) == set(manifest["files"]) | {"report-manifest.json"}
    assert result["files"] == len(manifest["files"]) + 1
    (output / "app.js").write_text("changed after validation", encoding="utf-8")
    with pytest.raises(ValueError, match="Report file changed"):
        package(output, tmp_path / "tampered.zip", visual_receipt=receipt)
    assert not (tmp_path / "tampered.zip").exists()


def test_full_review_profile_cannot_publish_machine_only_results(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    profile = read_json(home / "profile.json")
    profile["independent_result_review_scope"] = "all"
    save_json(home / "profile.json", profile)
    with pytest.raises(ValueError, match="Full-review profile requires"):
        build(
            tmp_path / "missing-review",
            home / "content.json",
            home=home,
            profile=home / "profile.json",
        )


@pytest.mark.parametrize("methods", [["alpha", "beta"], ["alpha", "beta", "gamma"]])
@pytest.mark.parametrize("tied", [False, True])
def test_interpretation_compares_every_registered_method_without_changing_scores(methods, tied):
    from harness.web_extract.report import audit_explanation

    fields = ("price", "sku", "availability", "OutOfStock")
    scores = {
        m: {f: {"f1": 0.5 if tied else (i + 1) / 10, "cases": 1} for f in fields}
        for i, m in enumerate(methods)
    }
    rows = [
        {
            "method": m,
            "case_id": "one",
            "compatibility": "structured_output",
            "field_results": {f: {"status": "correct"} for f in fields},
        }
        for m in methods
    ]
    core, _ = cohort_metrics(methods, rows, [{"certified": True, "scores": scores}], 1, "en")
    audit = {
        "case_count": 1,
        "result_count": len(rows),
        "original_results_reviewed": len(rows),
        "supplemental_results_reviewed": 0,
        "reference_changes": [],
        "reviewer": {"model": "synthetic"},
        "audited_scores": scores,
    }
    _, html = audit_explanation(audit, rows, methods, {}, "en", core_html=core)
    soup = check_presentation(html)
    comparison = soup.select("[data-interpretation-comparison] tbody tr")
    assert soup.select_one("[data-interpretation-comparison]").parent["class"] == ["table-wrap"]
    assert [r["data-method"] for r in comparison] == methods
    for i, row in enumerate(comparison):
        value = 0.5 if tied else (i + 1) / 10
        assert [c.get_text(strip=True) for c in row.select("td")[2:]] == [f"{value:.3f}"] * 4
        if tied:
            assert len(row.select(".comparison-winner")) == 4
    assert len(soup.select("[data-method-interpretation]")) == len(methods)


def test_mathematically_equal_f1_highlights_all_ties_despite_float_noise():
    def metrics(value):
        return {f: {"f1": value} for f in ("price", "sku", "availability", "OutOfStock")}

    bindings = [
        {"certified": True, "scores": {"a": metrics(2 / 3), "b": metrics(0.6666666666666667)}}
    ]
    html, _ = cohort_metrics(["a", "b"], [], bindings, 140, "en")
    assert len(check_presentation(html).select(".comparison-winner")) == 8
    bindings[0]["scores"] = {"a": metrics(0.8104), "b": metrics(0.8101)}
    html, _ = cohort_metrics(["a", "b"], [], bindings, 140, "en")
    assert len(check_presentation(html).select(".comparison-winner")) == 4


@pytest.mark.parametrize("mode", ["compatibility", "full-cohort-progress"])
def test_secret_only_in_html_is_rejected_even_with_valid_inventory(tmp_path, mode):
    create(tmp_path / "first", mode=mode)
    output = tmp_path / "first"
    page = output / "zh/dataset.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "</main>", "<p>Authorization: Bearer secret-token-only-in-html-123456</p></main>"
        ),
        encoding="utf-8",
    )
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/dataset.html"] = digest(page.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="artifact.*privacy"):
        check(output)


def test_report_defaults_and_saved_primary_use_shared_language_policy(tmp_path, monkeypatch):
    import harness.report_languages as languages

    home = create(tmp_path / "first")
    monkeypatch.setattr(languages, "REPORT_LANGUAGE_ENV_FILE", tmp_path / "no-env")
    monkeypatch.delenv("BROWSER_EVAL_REPORT_LANGUAGE", raising=False)
    output = tmp_path / "english-default"
    build(output, home / "content.json", home=home, profile=home / "profile.json")
    assert read_json(output / "report-manifest.json")["primary_language"] == "en"
    monkeypatch.setenv("BROWSER_EVAL_REPORT_LANGUAGE", "zh")
    assert languages.resolve_report_language(output) == "en"
    other = tmp_path / "configured-chinese"
    build(other, home / "content.json", home=home, profile=home / "profile.json")
    assert read_json(other / "report-manifest.json")["languages"] == ["zh"]


def test_configured_host_redaction_does_not_depend_on_one_deployment(tmp_path, monkeypatch):
    import harness.web_extract.report as report

    home = create(tmp_path / "first")
    source = home / "runs/example-v1"
    manifest = read_json(source / "manifest.json")
    entry = manifest["calls"][0]
    path = source / entry["path"]
    row = read_json(path)
    row["response"]["debug"] = "backend other-internal.example at 192.0.2.77"
    save_json(path, row)
    entry["sha256"] = digest(path.read_bytes())
    save_json(source / "manifest.json", manifest)
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / ".env").write_text(
        "WEB_EXTRACT_PUBLICATION_HOSTS=other-internal.example,192.0.2.77\n", encoding="utf-8"
    )
    monkeypatch.setattr(report, "repository_root", lambda: settings)
    output = tmp_path / "redacted"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    for path in (output / "public-evidence").glob("*.json"):
        assert "other-internal.example" not in path.read_text(encoding="utf-8")
        assert "192.0.2.77" not in path.read_text(encoding="utf-8")
    # No environment file is required for public/synthetic reports.
    monkeypatch.setattr(report, "repository_root", lambda: tmp_path / "absent-settings")
    assert report.publication_host_names() == ()


@pytest.mark.parametrize("damage", ["missing-english", "wrong-list-inventory"])
def test_report_rejects_incomplete_language_catalog(tmp_path, monkeypatch, damage):
    import copy

    import harness.web_extract.messages as messages

    home = create(tmp_path / "first")
    catalog = copy.deepcopy(messages.validate_catalog())
    if damage == "missing-english":
        catalog["en"].pop("historical_reproduction")
    else:
        catalog["en"]["navigation_labels"].pop()
    path = tmp_path / "messages.json"
    save_json(path, catalog)
    monkeypatch.setattr(messages, "MESSAGES", path)
    with pytest.raises(ValueError, match="catalog|translation inventory"):
        build(
            tmp_path / "invalid-catalog",
            home / "content.json",
            home=home,
            profile=home / "profile.json",
            language="zh",
        )


def test_report_release_binds_the_message_catalog(tmp_path):
    create(tmp_path / "report")
    output = tmp_path / "report"
    manifest = read_json(output / "report-manifest.json")
    manifest["messages_sha256"] = "0" * 64
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="message catalog changed"):
        check(output)


def test_usage_preserves_browser_receipts_in_separate_units():
    from harness.web_extract.report import usage_table

    rows = [
        {
            "method": "native",
            "provider_usage": {
                "X-Browser-Ms-Used": "125",
                "x-browser-time": "2.5",
                "x-browser-usage": "3",
            },
            "response": {"data": {"metadata": {"creditsUsed": 5}}},
        }
    ]
    soup = BeautifulSoup(usage_table(["native"], rows, "en"), "html.parser")
    cells = [c.get_text() for c in soup.select("tbody td")]
    assert cells[1:3] == ["1", "1"]
    assert all(
        value in cells[3]
        for value in (
            "5 credits",
            "125 ms (x-browser-ms-used)",
            "2.5 x-browser-time",
            "3 x-browser-usage",
        )
    )


def test_configured_private_host_is_projected_from_contract_and_rejected_on_check(tmp_path):
    from unittest.mock import patch

    home = create(tmp_path / "first", mode="full-cohort-progress")
    source = home / "runs/synthetic-success"
    contract = read_json(source / "contract.json")
    contract["fixture_base"] = "https://configured-fixture.example.test"
    save_json(source / "contract.json", contract)
    output = tmp_path / "public"
    with patch(
        "harness.web_extract.report.publication_host_names",
        return_value=("configured-fixture.example.test",),
    ):
        build(output, home / "content.json", home=home, profile=home / "profile.json")
        assert "configured-fixture.example.test" not in (output / "report-manifest.json").read_text(
            encoding="utf-8"
        )
        manifest = read_json(output / "report-manifest.json")
        manifest["source_runs"][0]["contract"]["fixture_base"] = contract["fixture_base"]
        save_json(output / "report-manifest.json", manifest)
        with pytest.raises(ValueError, match="metadata.*privacy"):
            check(output)


@pytest.mark.parametrize("data", [None, {"metadata": None}, {"metadata": []}])
def test_usage_handles_absent_native_metadata(data):
    from harness.web_extract.report import usage_table

    html = usage_table(["native"], [{"method": "native", "response": {"data": data}}], "en")
    cells = [c.get_text() for c in BeautifulSoup(html, "html.parser").select("tbody td")]
    assert cells[1:] == ["1", "0", "—"]


@pytest.mark.parametrize("ancestor", [False, True])
def test_checkpoint_build_already_rejects_symlinked_evidence(tmp_path, ancestor):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    source = home / "runs/synthetic-success"
    if ancestor:
        target = source / "calls"
        actual = tmp_path / "retained-calls"
        target.rename(actual)
        target.symlink_to(actual, target_is_directory=True)
    else:
        target = next((source / "calls").glob("*.json"))
        actual = tmp_path / "retained-call.json"
        target.rename(actual)
        target.symlink_to(actual)
    with pytest.raises(ValueError, match="Symlink"):
        build(
            tmp_path / "rejected", home / "content.json", home=home, profile=home / "profile.json"
        )


def test_package_rejects_unbound_nested_manifest(tmp_path):
    from harness.web_extract.report import package

    output = tmp_path / "report"
    create(output)
    (output / "private").mkdir()
    (output / "private/report-manifest.json").write_text(
        '{"unbound":"private bytes"}', encoding="utf-8"
    )
    save_json(tmp_path / "unused.json", {})
    with pytest.raises(ValueError, match="file inventory differs"):
        package(output, tmp_path / "report.zip", visual_receipt=tmp_path / "unused.json")
    assert not (tmp_path / "report.zip").exists()


def test_audited_f1_exposes_each_field_population_and_exclusions():
    from harness.web_extract.report import audit_explanation

    fields = ("price", "sku", "availability", "OutOfStock")
    scores = {
        "native": {field: {"f1": 1.0, "cases": 2 if field == "sku" else 1} for field in fields}
    }
    rows = [
        {
            "method": "native",
            "case_id": cid,
            "compatibility": "structured_output",
            "field_results": {field: {"status": "correct"} for field in fields},
        }
        for cid in ("one", "two")
    ]
    audit = {
        "case_count": 2,
        "result_count": 2,
        "original_results_reviewed": 2,
        "supplemental_results_reviewed": 0,
        "reference_changes": [],
        "reviewer": {"model": "synthetic"},
        "audited_scores": scores,
        "excluded_fields": {"one": ["price", "availability"]},
    }
    review, _ = audit_explanation(audit, rows, ["native"], {}, "en")
    soup = BeautifulSoup(review, "html.parser")
    assert [n.get_text() for n in soup.select("[data-audited-population]")] == [
        "Scored 1/2; excluded 1",
        "Scored 2/2; excluded 0",
        "Scored 1/2; excluded 1",
        "Scored 1/2; excluded 1",
    ]
    assert "original-reference scores retain the complete population" in soup.get_text()
    from harness.web_extract.report import check_audited_quality

    check_audited_quality(soup, ["native"], scores, 2, "en")
    note = soup.select_one("[data-audited-population]")
    note.decompose()
    with pytest.raises(ValueError, match="field population"):
        check_audited_quality(soup, ["native"], scores, 2, "en")
    scores["native"]["price"]["cases"] = 0
    review, _ = audit_explanation(audit, rows, ["native"], {}, "en")
    soup = BeautifulSoup(review, "html.parser")
    first = soup.select_one("[data-audited-quality] tbody tr td:nth-child(2)")
    check_presentation(soup)
    assert list(first.stripped_strings) == ["—", "Scored 0/2; excluded 2"]
    assert not first.select_one(".comparison-winner")
    check_audited_quality(soup, ["native"], scores, 2, "en")


def test_chinese_scraper_report_is_byte_identical_under_ascii_locale(tmp_path):
    import subprocess
    import sys

    from tests.test_explicit_utf8_io import ascii_locale_environment

    report = tmp_path / "utf8-report"
    home = create(report)
    expected = {
        p.relative_to(report).as_posix(): p.read_bytes()
        for p in report.rglob("*")
        if p.suffix in (".html", ".js", ".css")
    }
    ascii_report = tmp_path / "ascii-report"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness.web_extract.cli",
            "--home",
            str(home),
            "report",
            "--content",
            str(home / "content.json"),
            "--profile",
            str(home / "profile.json"),
            "--output",
            str(ascii_report),
            "--language",
            "zh",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=ascii_locale_environment(),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = {
        p.relative_to(ascii_report).as_posix(): p.read_bytes()
        for p in ascii_report.rglob("*")
        if p.suffix in (".html", ".js", ".css")
    }
    assert actual == expected


@pytest.mark.parametrize("damage", ["missing", "moving-branch"])
def test_report_dataset_download_link_is_pinned_and_release_checked(tmp_path, monkeypatch, damage):
    from harness.web_extract import dataset

    home = create(tmp_path / "example")
    content = read_json(home / "content.json")
    content["dataset_distribution"] = {"track": "web-scraper", "revision": "a" * 40}
    save_json(home / "content.json", content)
    url = "https://huggingface.co/datasets/Lexmount/LexBench-Web-Scraper/tree/" + "a" * 40
    monkeypatch.setattr(
        dataset,
        "distribution_identity",
        lambda *a: {
            "track": "web-scraper",
            "revision": "a" * 40,
            "url": url,
        },
    )
    output = tmp_path / "linked"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="en")
    manifest = read_json(output / "report-manifest.json")
    assert manifest["dataset_distribution"]["revision"] == "a" * 40
    for language in ("en", "zh"):
        path = output / language / "dataset.html"
        text = path.read_text(encoding="utf-8")
        assert url in text
    path = output / "zh/dataset.html"
    text = path.read_text(encoding="utf-8")
    text = (
        text.replace("data-dataset-distribution", "removed")
        if damage == "missing"
        else text.replace(url, url.rsplit("/", 1)[0] + "/main")
    )
    path.write_text(text, encoding="utf-8")
    manifest["files"]["zh/dataset.html"] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="Pinned dataset download link"):
        check(output)


@pytest.mark.parametrize("suffix", [".svg", ".xml", ".jsonl", ".unknown"])
@pytest.mark.parametrize("sensitive", [False, True])
def test_all_asset_formats_receive_publication_privacy_checks(tmp_path, suffix, sensitive):
    home = create(tmp_path / "first")
    source = home / ("diagram" + suffix)
    source.write_text(
        "<svg><text>"
        + (
            "Authorization: Bearer secret-token-only-in-asset-123456"
            if sensitive
            else "Frozen HTML → native API → fields"
        )
        + "</text></svg>",
        encoding="utf-8",
    )
    content = read_json(home / "content.json")
    content["assets"] = [
        {
            "source": source.name,
            "path": "assets/" + source.name,
            "sha256": digest(source.read_bytes()),
        }
    ]
    save_json(home / "content.json", content)
    kwargs = dict(home=home, profile=home / "profile.json")
    if sensitive:
        with pytest.raises(ValueError, match="publication privacy risk"):
            build(tmp_path / "new", home / "content.json", **kwargs)
    else:
        build(tmp_path / "new", home / "content.json", **kwargs)
        check(tmp_path / "new")


def test_published_release_cannot_drop_dataset_distribution_binding(tmp_path):
    output = tmp_path / "report"
    home = create(tmp_path / "fixture", mode="full-cohort-progress")
    profile = read_json(home / "profile.json")
    profile["local_example"] = False
    save_json(home / "profile.json", profile)
    build(output, home / "content.json", home=home, profile=home / "profile.json")
    manifest = read_json(output / "report-manifest.json")
    manifest["dataset_kind"] = "published-upstream"
    manifest["dataset_distribution"] = None
    for value in manifest["prices"].values():
        value["usd_per_1000_range"] = [0, 0]
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="pinned dataset download binding"):
        check(output, release=True)


def test_field_headers_count_both_nonempty_and_absent_references():
    truth = {
        "one": {"price": ["3"], "sku": ["x"], "InStock": ["InStock"]},
        "two": {"price": [], "sku": [], "OutOfStock": ["OutOfStock"]},
    }
    html, _ = cohort_metrics(["a"], [], [], 2, "en", truth=truth)
    soup = BeautifulSoup(html, "html.parser")
    counts = soup.select("[data-reference-population]")
    assert len(counts) == 4
    assert all("Scored: 2 pages" in node.get_text() for node in counts)
    assert "positive 1, negative 1" in counts[-1].get_text()


@pytest.mark.parametrize("kind", [None, "", "other", "published-upstream"])
def test_release_cannot_change_dataset_kind_independently_of_input_profile(tmp_path, kind):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    if kind is None:
        manifest.pop("dataset_kind")
    else:
        manifest["dataset_kind"] = kind
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="dataset kind.*bound input profile"):
        check(output, release=True)


def test_recorded_profile_change_cannot_be_hidden_by_resealing_file_inventory(tmp_path):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    path = output / manifest["input_profile"]["path"]
    profile = read_json(path)
    profile["local_example"] = False
    save_json(path, profile)
    manifest["files"][manifest["input_profile"]["path"]] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="input profile binding"):
        check(output, release=True)


@pytest.mark.parametrize("completed", [False, True])
def test_formal_release_requires_certification_even_for_complete_checkpoints(tmp_path, completed):
    output = tmp_path / "report"
    home = create(output, mode="full-cohort-progress")
    if completed:
        import json

        model = read_json(home / "content.json")
        for selection in model["evidence_runs"]:
            source = home / "runs" / selection["run"]
            method = read_json(source / "contract.json")["methods"][0]
            entries = []
            for cid in read_json(source / "contract.json")["case_ids"]:
                original = home / "runs/example-v1/calls" / (method + "-" + cid + ".json")
                path = source / "calls" / original.name
                save_json(path, read_json(original))
                entries.append({"path": "calls/" + path.name, "sha256": digest(path.read_bytes())})
            (source / "calls.journal.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
            )
    profile = read_json(home / "profile.json")
    profile["local_example"] = False
    save_json(home / "profile.json", profile)
    output = tmp_path / "declared-upstream"
    build(output, home / "content.json", home=home, profile=home / "profile.json")
    manifest = read_json(output / "report-manifest.json")
    manifest["dataset_kind"] = "published-upstream"
    manifest["dataset_distribution"] = {"revision": "a" * 40}
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="complete certified cohort"):
        check(output, release=True)


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_interpretation_uses_program_error_and_never_invents_returned_fields(lang):
    from harness.web_extract.report import audit_explanation

    fields = ("price", "sku", "availability", "OutOfStock")
    scores = {"extruct": {f: {"f1": 0, "cases": 1} for f in fields}}
    rows = [
        {
            "method": "extruct",
            "case_id": "one",
            "compatibility": "no_structured_output",
            "native_error": "EXACT TRANSPORT ERROR",
            "field_results": {f: {"status": "missing"} for f in fields},
        }
    ]
    audit = {
        "case_count": 1,
        "result_count": 1,
        "original_results_reviewed": 1,
        "supplemental_results_reviewed": 0,
        "reference_changes": [],
        "reviewer": {"model": "synthetic"},
        "audited_scores": scores,
        "excluded_fields": {"one": ["price"]},
    }
    review, interpretation = audit_explanation(audit, rows, ["extruct"], {}, lang)
    assert "EXACT TRANSPORT ERROR" in interpretation
    assert "Fields were returned" not in interpretation
    assert "these_differences_concern" not in interpretation
    assert "The upstream baseline emits" not in interpretation
    if lang == "zh":
        headers = BeautifulSoup(review, "html.parser").select("table")[-1].select("th")
        assert [h.get_text() for h in headers] == ["题目", "字段"]
        assert "价格" in BeautifulSoup(review, "html.parser").select("table")[-1].get_text()


@pytest.mark.parametrize("payload", [{}, {"error": {"type": "t", "message": "m"}}])
def test_failed_details_use_the_same_error_as_dialog_evidence(tmp_path, payload):
    import json

    from harness.web_extract.analysis import native_error
    from harness.web_extract.messages import message

    home = create(tmp_path / "first")
    source = home / "runs/example-v1"
    manifest = read_json(source / "manifest.json")
    for entry in manifest["calls"]:
        path = source / entry["path"]
        row = read_json(path)
        if row["method"] != "synthetic-blocked":
            continue
        row.update(error="", response=payload, state="response_received", http_status=422)
        save_json(path, row)
        entry["sha256"] = digest(path.read_bytes())
    save_json(source / "manifest.json", manifest)
    output = tmp_path / "rebuilt"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="en")
    for lang in ["zh", "en"]:
        for path in (output / lang).glob("result-synthetic-blocked-*.html"):
            page = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            expected = native_error({"response": payload}) or message(
                "no_recognizable_product_fields_returned", lang
            )
            assert page.select_one('pre[data-kind="error"]').get_text() == expected
            if payload:
                assert expected == json.dumps(payload["error"], ensure_ascii=False)
