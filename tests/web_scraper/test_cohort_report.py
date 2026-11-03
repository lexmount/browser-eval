"""Full planned populations, checkpoint evidence and certified quality presentation."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_scraper.example import create
from harness.web_scraper.pipeline import digest, read_json, save_json
from harness.web_scraper.report import build, check, cohort_metrics


def test_progress_includes_unexecuted_cases_without_inventing_calls(tmp_path):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    assert manifest["observations"] == 4
    assert len(manifest["planned_methods"]) == 2
    inventory = BeautifulSoup((output / "zh/results.html").read_text(), "html.parser")
    assert len(inventory.select("tr[data-status]")) == 6
    assert len(inventory.select('tr[data-status="pending"]')) == 2
    home = BeautifulSoup((output / "zh/index.html").read_text(), "html.parser")
    assert all(
        c.get_text(strip=True) == "—"
        for c in home.select("[data-core-metrics] tbody td:nth-child(n+6)")
    )
    assert check(output)["state"] == "full-cohort-progress"
    # Evidence is grouped by method, loaded on demand, rather than copied per detail.
    assert len(list((output / "public-evidence").glob("*.json"))) == 2
    assert "data-key=" in next((output / "zh").glob("result-*.html")).read_text()


def test_checkpoint_rejects_modified_calls_and_missing_participants(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    content = read_json(home / "content.json")
    source = home / "runs/synthetic-success"
    call = next((source / "calls").glob("*.json"))
    original = call.read_bytes()
    call.write_text("{}")
    with pytest.raises(ValueError, match="Checkpoint call changed"):
        build(
            tmp_path / "tampered", home / "content.json", home=home, profile=home / "profile.json"
        )
    call.write_bytes(original)
    content["evidence_runs"].pop()
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="missing a planned method"):
        build(tmp_path / "missing", home / "content.json", home=home, profile=home / "profile.json")


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
    metadata = (output / "report-manifest.json").read_text()
    assert "private-pass" not in metadata
    assert "private-token" not in metadata
    manifest = read_json(output / "report-manifest.json")
    manifest["source_runs"][0]["contract"]["fixture_base"] = contract["fixture_base"]
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="metadata.*privacy"):
        check(output)


def test_cost_prices_and_latency_cannot_omit_a_compared_participant(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
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
    soup = BeautifulSoup(page.read_text(), "html.parser")
    soup.select_one("[data-core-metrics] tbody tr td:nth-child(3)").string = "99"
    page.write_text(str(soup))
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/index.html"] = digest(page.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="progress count differs"):
        check(output)


def test_quality_highlights_raw_unique_extrema_but_leaves_ties_neutral():
    def metrics(n):
        return {f: {"f1": n} for f in ("price", "sku", "availability", "OutOfStock")}

    bindings = [{"certified": True, "scores": {"a": metrics(0.81), "b": metrics(0.80)}}]
    body, _ = cohort_metrics(["a", "b", "pending"], [], bindings, 140, "zh")
    soup = check_presentation(body)
    assert len(soup.select(".comparison-winner")) == 4
    assert soup.select("tbody tr")[-1].select("td")[-1].get_text() == "—"
    bindings[0]["scores"]["b"] = metrics(0.81)
    body, _ = cohort_metrics(["a", "b"], [], bindings, 140, "zh")
    assert not check_presentation(body).select(".comparison-winner")


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
    from harness.web_scraper.report import usage_table

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
    soup = BeautifulSoup(page.read_text(), "html.parser")
    cell = soup.select_one(selector)
    marker = cell.select_one(".comparison-winner")
    (marker if marker is not None else cell).string = "9999"
    page.write_text(str(soup))
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
    soup = BeautifulSoup((output / "zh/index.html").read_text(), "html.parser")
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
    soup = BeautifulSoup(path.read_text(), "html.parser")
    soup.select_one(selector).decompose()
    path.write_text(str(soup))
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/" + route] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="Home latency|filters|Field diagnostics|same-case"):
        check(output)


def test_package_checks_release_and_includes_only_the_frozen_public_tree(tmp_path):
    import zipfile
    from unittest.mock import patch

    from harness.web_scraper.report import package

    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    (tmp_path / "private-secret.txt").write_text("Never package siblings")
    receipt = tmp_path / "visual.json"
    with patch("harness.web_scraper.report.check") as release_check:
        result = package(output, tmp_path / "public.zip", visual_receipt=receipt)
    release_check.assert_called_once_with(output, release=True, visual_receipt=receipt)
    with zipfile.ZipFile(tmp_path / "public.zip") as bundle:
        assert set(bundle.namelist()) == set(manifest["files"]) | {"report-manifest.json"}
    assert result["files"] == len(manifest["files"]) + 1
    (output / "app.js").write_text("changed after validation")
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
def test_interpretation_compares_every_registered_method_without_changing_scores(methods):
    from harness.web_scraper.report import audit_explanation

    fields = ("price", "sku", "availability", "OutOfStock")
    scores = {m: {f: {"f1": (i + 1) / 10} for f in fields} for i, m in enumerate(methods)}
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
    assert [r["data-method"] for r in comparison] == methods
    for i, row in enumerate(comparison):
        assert [c.get_text(strip=True) for c in row.select("td")[2:]] == [f"{(i + 1) / 10:.3f}"] * 4
    assert len(soup.select("[data-method-interpretation]")) == len(methods)


def test_mathematically_equal_f1_is_neutral_despite_float_arithmetic_noise():
    def metrics(value):
        return {f: {"f1": value} for f in ("price", "sku", "availability", "OutOfStock")}

    bindings = [
        {"certified": True, "scores": {"a": metrics(2 / 3), "b": metrics(0.6666666666666667)}}
    ]
    html, _ = cohort_metrics(["a", "b"], [], bindings, 140, "en")
    assert not check_presentation(html).select(".comparison-winner")
    bindings[0]["scores"] = {"a": metrics(0.8104), "b": metrics(0.8101)}
    html, _ = cohort_metrics(["a", "b"], [], bindings, 140, "en")
    assert len(check_presentation(html).select(".comparison-winner")) == 4
