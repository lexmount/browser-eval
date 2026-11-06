"""Whole-page delivery and absent-field handling from retained responses."""

from copy import deepcopy

import pytest

from harness.web_extract.analysis import completion_metrics, record_field_results


def product(actual, *, status=200):
    from harness.web_extract.pipeline import normalize

    response = {"data": {"json": actual}}
    return {
        "case_id": "page",
        "method": "firecrawl-json",
        "http_status": status,
        "compatibility": "structured_output",
        "response": response,
        "normalized_fields": normalize("firecrawl-json", response),
    }


@pytest.mark.parametrize("status,expected", [(200, 1), (500, 0), (None, 0)])
def test_failed_calls_never_pass_an_all_empty_reference(status, expected):
    row = product({"price": None, "sku": None, "availability": None}, status=status)
    fields = {
        "page": {f: {"expected": [], "status": "correct"} for f in ("price", "sku", "availability")}
    }
    result = completion_metrics([row], fields, records=False)
    assert result["page_exact"] == {"correct": expected, "total": 1, "rate": float(expected)}
    assert result["empty_fields"]["correct"] == 3 * expected


@pytest.mark.parametrize("bad", [True, [], {}, "not a price", "NaN", "Infinity"])
def test_discarded_or_invalid_price_is_not_a_correct_empty_value(bad):
    row = product({"price": bad, "sku": None, "availability": None})
    fields = {
        "page": {f: {"expected": [], "status": "correct"} for f in ("price", "sku", "availability")}
    }
    result = completion_metrics([row], fields, records=False)
    assert result["page_exact"]["correct"] == 0
    assert result["empty_fields"]["correct"] == 2


@pytest.mark.parametrize("stock,expected", [(None, 1), ([], 0), ({}, 0), (False, 0), (12, 0)])
def test_native_scrapfly_stock_types_are_not_hidden_by_legacy_projection(stock, expected):
    from harness.web_extract.pipeline import normalize

    payload = {"data": {"offers": [{"availability": stock}], "identifiers": {}}}
    row = {
        "case_id": "page",
        "method": "scrapfly-product",
        "http_status": 200,
        "compatibility": "structured_output",
        "response": payload,
        "normalized_fields": normalize("scrapfly-product", payload),
    }
    fields = {
        "page": {f: {"expected": [], "status": "correct"} for f in ("price", "sku", "availability")}
    }
    assert (
        normalize("scrapfly-product", payload, preserve_native_types=True)["availability"] == stock
    )
    result = completion_metrics([row], fields, records=False)
    assert result["page_exact"]["correct"] == expected
    assert result["empty_fields"]["correct"] == 2 + expected


@pytest.mark.parametrize("actual,expected", [([], 1), (["extra"], 0), (None, 0), (False, 0)])
def test_empty_list_requires_a_valid_delivered_array(actual, expected):
    ref = {"values": {"authors": []}}
    row = {
        "case_id": "page",
        "http_status": 200,
        "compatibility": "structured_output",
        "normalized_fields": {"authors": actual},
    }
    fields = {"page": record_field_results(ref, row)}
    result = completion_metrics([row], fields, records=True)
    assert result["page_exact"]["correct"] == expected
    assert result["empty_fields"]["correct"] == expected


def test_missing_required_array_is_not_a_correct_empty_field():
    row = {
        "case_id": "page",
        "http_status": 200,
        "compatibility": "structured_output",
        "normalized_fields": {},
    }
    fields = {"page": record_field_results({"values": {"authors": []}}, row)}
    assert fields["page"]["authors"]["exact"] is True  # historical field diagnostic
    result = completion_metrics([row], fields, records=True)
    assert result["page_exact"]["rate"] == 0
    assert result["empty_fields"]["rate"] == 0


def test_partial_reference_excludes_whole_page_but_retains_covered_empty_fields():
    row = {
        "case_id": "page",
        "http_status": 200,
        "compatibility": "structured_output",
        "normalized_fields": {"authors": [], "title": []},
    }
    fields = {
        "page": record_field_results(
            {"values": {"authors": [], "title": []}}, row, unavailable_fields=["title"]
        )
    }
    result = completion_metrics([row], fields, records=True)
    assert result["page_exact"] == {"correct": 0, "total": 0, "rate": None}
    assert result["excluded_pages"] == 1
    assert result["empty_fields"] == {"correct": 1, "total": 1, "rate": 1}


@pytest.mark.parametrize(
    "actual,exact", [(["A", "B"], 1), (["A"], 0), (["A", "B", "C"], 0), (["A", "B", "B"], 0)]
)
def test_page_requires_all_values_and_no_extras(actual, exact):
    row = {
        "case_id": "page",
        "http_status": 200,
        "compatibility": "structured_output",
        "normalized_fields": {"authors": actual},
    }
    fields = {"page": record_field_results({"values": {"authors": ["A", "B"]}}, row)}
    result = completion_metrics([row], fields, records=True)
    assert result["page_exact"]["correct"] == exact
    assert result["empty_fields"]["rate"] is None


def test_metric_requires_complete_unique_population_and_preserves_inputs():
    row = product({"price": "10", "sku": "A", "availability": "InStock"})
    fields = {
        "page": {
            f: {"expected": ["value"], "status": "correct"}
            for f in ("price", "sku", "availability")
        }
    }
    before = deepcopy((row, fields))
    completion_metrics([row], fields, records=False)
    assert (row, fields) == before
    for rows in [[], [row, row]]:
        with pytest.raises(ValueError, match="complete unique"):
            completion_metrics(rows, fields, records=False)


@pytest.mark.parametrize(
    "rates,winners", [([0.5, 0.5, 0.3], []), ([0.5, 0.7, 0.3], [1]), ([None, None, None], [])]
)
def test_completion_comparison_marks_only_a_unique_leader(rates, winners):
    from bs4 import BeautifulSoup

    from harness.reporting.presentation import check_presentation
    from harness.web_extract.report import completion_table

    methods = ["first", "second", "third"]
    bundle = {"reference_view": "original", "methods": {}}
    for method, rate in zip(methods, rates, strict=True):
        metric = {
            "correct": int(10 * rate) if rate is not None else 0,
            "total": 10 if rate is not None else 0,
            "rate": rate,
        }
        bundle["methods"][method] = {
            "page_exact": metric,
            "empty_fields": metric,
            "excluded_pages": 0,
        }
    soup = BeautifulSoup(completion_table(bundle, methods, {}, "en"), "html.parser")
    for column in (1, 2):
        assert [
            i
            for i, row in enumerate(soup.select("tbody tr"))
            if row.select("td")[column].select_one(".comparison-winner")
        ] == winners
    check_presentation(soup)


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
@pytest.mark.parametrize("damage", ["comparison", "definition", "count", "coverage", "highlight"])
def test_release_check_rejects_misleading_supplemental_metrics(tmp_path, mode, damage):
    from bs4 import BeautifulSoup

    from harness.web_extract.example import create
    from harness.web_extract.pipeline import digest, read_json, save_json
    from harness.web_extract.report import check

    output = tmp_path / "report"
    create(output, mode=mode)
    manifest_path = output / "report-manifest.json"
    manifest = read_json(manifest_path)
    if damage in ("count", "coverage"):
        path = output / "data/completion-metrics.json"
        data = read_json(path)
        if damage == "count":
            data["methods"][next(iter(data["methods"]))] = {"page_exact": {"correct": 999}}
        else:
            data["excluded_fields"] = {"substituted-page": ["substituted-field"]}
        save_json(path, data)
    else:
        path = output / "zh/index.html"
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        section = soup.select_one("[data-completion-metrics]")
        if damage == "comparison":
            section.decompose()
        elif damage == "definition":
            section.select_one("p").string = "Failures and missing references receive full credit."
        else:
            section.select_one("tbody td:nth-child(2)")["class"] = "comparison-winner"
        path.write_text(str(soup), encoding="utf-8")
    # Exercise semantic verification, rather than merely failing the file hash.
    manifest["files"][path.relative_to(output).as_posix()] = digest(path.read_bytes())
    save_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="Completion"):
        check(output)


def test_incomplete_cohort_is_distinct_from_a_computed_empty_denominator():
    from harness.web_extract.report import completion_table

    bundle = {"reference_view": "original", "methods": {"pending": None}}
    text = completion_table(bundle, ["pending"], {}, "en")
    assert "complete certification pending" in text
    assert "denominator 0" not in text


def test_changing_both_coverage_metadata_copies_cannot_override_audit(tmp_path):
    from harness.web_extract.pipeline import digest, save_json
    from harness.web_extract.report import (
        check_completion_metrics,
        completion_bundle,
        completion_table,
    )

    row = product({"price": "5", "sku": "wrong", "availability": "InStock"})
    row["field_results"] = {
        f: {"expected": ["reference"], "status": "wrong"} for f in ("price", "sku", "availability")
    }
    rows = [row]
    audit_path = tmp_path / "public-evidence/audit.json"
    save_json(audit_path, {"result_count": 1, "excluded_fields": {}})
    sha = digest(audit_path.read_bytes())
    manifest = {
        "mode": "full-cohort-progress",
        "languages": ["zh"],
        "observations": 1,
        "planned_methods": ["firecrawl-json"],
        "method_labels": {},
        "source_runs": [{"certified": True, "scores": {"firecrawl-json": {}}}],
        "completion_reference_exclusions": {},
        "completion_audit": {
            "path": "public-evidence/audit.json",
            "sha256": sha,
            "source_sha256": sha,
        },
        "public_evidence_views": [{"path": "public-evidence/audit.json", "original_sha256": sha}],
    }

    def regenerate(exclusions):
        bundle = completion_bundle(
            ["firecrawl-json"],
            rows,
            records=False,
            complete_methods=["firecrawl-json"],
            excluded_fields=exclusions,
        )
        save_json(tmp_path / "data/completion-metrics.json", bundle)
        for route in ("index", "interpretation"):
            path = tmp_path / "zh" / (route + ".html")
            path.parent.mkdir(exist_ok=True)
            html = completion_table(bundle, ["firecrawl-json"], {}, "zh").replace(
                'href="../data/', 'href="data/'
            )
            path.write_text(html, encoding="utf-8")

    regenerate({})
    check_completion_metrics(tmp_path, manifest, rows)
    manifest["completion_reference_exclusions"] = {"page": ["sku"]}
    regenerate(manifest["completion_reference_exclusions"])
    with pytest.raises(ValueError, match="authoritative audit"):
        check_completion_metrics(tmp_path, manifest, rows)
