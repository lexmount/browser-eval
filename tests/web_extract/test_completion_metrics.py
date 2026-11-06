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
        soup = BeautifulSoup(path.read_text(), "html.parser")
        section = soup.select_one("[data-completion-metrics]")
        if damage == "comparison":
            section.decompose()
        elif damage == "definition":
            section.select_one("p").string = "Failures and missing references receive full credit."
        else:
            section.select_one("tbody td:nth-child(2)")["class"] = "comparison-winner"
        path.write_text(str(soup))
    # Exercise semantic verification, rather than merely failing the file hash.
    manifest["files"][path.relative_to(output).as_posix()] = digest(path.read_bytes())
    save_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="Completion"):
        check(output)
