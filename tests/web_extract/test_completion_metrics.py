"""Whole-page delivery and absent-field handling from retained responses."""

import json
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
            section.select_one("th[data-completion-metric]").decompose()
        elif damage == "definition":
            soup.select_one(
                "#metric-completion-page_exact td:nth-child(2)"
            ).string = "Failures and missing references receive full credit."
        else:
            section.select_one("td[data-completion-metric]")["class"] = "comparison-winner"
        path.write_text(str(soup), encoding="utf-8")
    # Exercise semantic verification, rather than merely failing the file hash.
    manifest["files"][path.relative_to(output).as_posix()] = digest(path.read_bytes())
    save_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="Completion|Metric guide"):
        check(output)


def test_incomplete_cohort_is_distinct_from_a_computed_empty_denominator():
    from harness.web_extract.report import completion_columns

    bundle = {"reference_view": "original", "methods": {"pending": None}}
    text = str(completion_columns(bundle, ["pending"], "en"))
    assert "complete certification pending" in text
    assert "denominator 0" not in text


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
def test_new_metrics_share_the_core_table_and_explain_their_origin(tmp_path, mode):
    from bs4 import BeautifulSoup

    from harness.web_extract.example import create

    output = tmp_path / "report"
    create(output, mode=mode)
    for route in ("index", "interpretation"):
        soup = BeautifulSoup(
            (output / "zh" / (route + ".html")).read_text(encoding="utf-8"), "html.parser"
        )
        tables = soup.select("table[data-completion-metrics]")
        assert len(tables) == 1
        assert not soup.select("section[data-completion-metrics]")
        assert len(tables[0].select("th[data-completion-metric]")) == 2
        assert "本项目补充" in tables[0].get_text()
        assert any(
            "F1" in th.get_text() for th in tables[0].select("th:not([data-completion-metric])")
        )
    home = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    page = home.select_one("#metric-completion-page_exact").get_text()
    empty = home.select_one("#metric-completion-empty_fields").get_text()
    assert all(
        term in page for term in ("来源：本项目补充定义", "公式", "3/8", "37.5%", "参考完整")
    )
    assert all(
        term in empty for term in ("来源：本项目补充定义", "公式", "4/5", "80%", "请求失败算错")
    )


@pytest.mark.parametrize("damage", ["coverage", "judgment"])
def test_completion_cannot_override_authoritative_audit(tmp_path, damage):
    from harness.web_extract.pipeline import digest, save_json
    from harness.web_extract.report import (
        check_completion_metrics,
        completion_bundle,
        with_completion_columns,
    )

    row = product({"price": "5", "sku": "wrong", "availability": "InStock"})
    row["field_results"] = {
        f: {"expected": ["reference"], "status": "wrong"} for f in ("price", "sku", "availability")
    }
    row["review"] = {"audited_fields": deepcopy(row["field_results"])}
    rows = [row]
    audit_path = tmp_path / "public-evidence/audit.json"
    save_json(
        audit_path,
        {
            "result_count": 1,
            "excluded_fields": {},
            "results": {
                "firecrawl-json:page": {
                    **row["review"],
                    "native_response_sha256": digest(
                        json.dumps(row["response"], sort_keys=True, ensure_ascii=False).encode()
                    ),
                }
            },
        },
    )
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
            base = (
                '<div class="table-wrap"><table data-core-metrics data-interpretation-comparison>'
                "<thead><tr><th>Method</th></tr></thead>"
                "<tbody><tr><td>Firecrawl</td></tr></tbody></table></div>"
            )
            html = with_completion_columns(base, bundle, ["firecrawl-json"], "zh")
            path.write_text(html, encoding="utf-8")

    regenerate({})
    check_completion_metrics(tmp_path, manifest, rows)
    if damage == "coverage":
        manifest["completion_reference_exclusions"] = {"page": ["sku"]}
    elif damage == "judgment":
        row["review"]["audited_fields"]["sku"]["status"] = "correct"
    else:
        row["response"]["data"]["json"]["sku"] = "substituted"
    regenerate(manifest["completion_reference_exclusions"])
    with pytest.raises(ValueError, match="authoritative audit"):
        check_completion_metrics(tmp_path, manifest, rows)


@pytest.mark.parametrize('key,replacement', [('page_exact', 1), ('empty_fields_correct', False)])
def test_completion_receipt_preserves_boolean_and_count_types(tmp_path, key, replacement):
    from harness.web_extract.pipeline import save_json
    from harness.web_extract.report import check_completion_metrics, completion_bundle

    row = product({'price': '5', 'sku': 'ABC', 'availability': 'InStock'})
    row['field_results'] = {
        f: {'expected': [v], 'status': 'correct'}
        for f, v in row['normalized_fields'].items()
    }
    methods = ['firecrawl-json']
    bundle = completion_bundle(methods, [row], records=False, complete_methods=methods)
    manifest = {
        'mode': 'full-cohort-progress', 'planned_methods': methods, 'languages': [],
        'source_runs': [{'certified': True, 'scores': {'firecrawl-json': {}}}],
    }
    path = tmp_path / 'data/completion-metrics.json'
    save_json(path, bundle)
    check_completion_metrics(tmp_path, manifest, [row])
    bundle['methods']['firecrawl-json']['cases']['page'][key] = replacement
    save_json(path, bundle)
    with pytest.raises(ValueError, match='differ from retained final responses'):
        check_completion_metrics(tmp_path, manifest, [row])
