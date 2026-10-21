"""Comparison filters and highlights use the same scoped factual evidence as totals."""

import copy
import os

import pytest

from harness.reporting.visual import browser_executable
from harness.web_search.comparison import comparison_data, render_comparison


def scenarios():
    cases = [
        {
            "id": cid,
            "query": f"Who founded {cid}?",
            "reference_answer": "Ada",
            "reference_urls": [f"https://history.test/{cid}"],
        }
        for cid in ("isolated", "leaked", "genuine")
    ]
    profile = {
        "methods": [{"id": m, "label": m.upper()} for m in ("a", "b")],
        "cases": cases,
        "factual_quality_exclusions": ["isolated"],
    }
    rows = []
    for case in cases:
        for m in ("a", "b"):
            item = {
                "url": case["reference_urls"][0],
                "title": "Original page",
                "snippet": "Ada founded this college.",
                "summary": "Full summary retained",
                "content": "Full body retained",
            }
            if case["id"] == "leaked":
                item.update(
                    title="Public record",
                    content=case["query"] + " Ada Subsets and Splits SELECT * FROM train LIMIT 10",
                )
            rows.append(
                {
                    "id": case["id"] + m,
                    "case_id": case["id"],
                    "method": m,
                    "results": [item],
                    "metrics": {"labeled_source_hit_5": 1, "answer_in_delivered_text_5": 1},
                }
            )
    return profile, rows


def test_factual_quality_checks_share_exclusions_and_v3_without_mutating_inputs():
    profile, rows = scenarios()
    original = copy.deepcopy((profile, rows))
    data = comparison_data(profile, rows)
    isolated, leaked, genuine = data["cases"]
    for out in isolated["outputs"].values():
        assert all(out[k] is None for k in ("primary", "source", "answer"))
        assert out["availability"] is True
        assert out["items"][0]["source_match"] is None
        assert all(v is None for v in out["items"][0]["answer_fields"].values())
    for out in leaked["outputs"].values():
        assert all(out[k] is False for k in ("primary", "source", "answer"))
        assert out["availability"] is True
        assert not out["items"][0]["source_match"]
        assert not any(out["items"][0]["answer_fields"].values())
        assert out["items"][0]["summary"] == "Full summary retained"
        assert out["items"][0]["content"].endswith("LIMIT 10")
    for out in genuine["outputs"].values():
        assert all(out[k] is True for k in ("primary", "source", "answer", "availability"))
        assert out["items"][0]["answer_fields"]["snippet"] is True
    assert (profile, rows) == original


def test_late_contamination_markers_cannot_restore_green_in_truncated_body_preview():
    profile, rows = scenarios()
    for row in rows:
        if row["case_id"] == "leaked":
            row["results"][0]["content"] = "Ada " + "padding " * 120 + row["results"][0]["content"]
    out = comparison_data(profile, rows)["cases"][1]["outputs"]["a"]
    assert out["answer"] is False
    assert not any(out["items"][0]["answer_fields"].values())
    assert not out["items"][0]["answer_outside_preview"]


@pytest.mark.parametrize("zh", [False, True])
def test_real_browser_filters_and_highlights_respect_quality_scope(zh):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        binary = os.environ.get("BROWSER_EVAL_TEST_BROWSER_BINARY") or browser_executable()
    except ValueError:
        pytest.skip("Install Chromium or set BROWSER_EVAL_TEST_BROWSER_BINARY")
    profile, rows = scenarios()
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=binary, headless=True)
        page = browser.new_page()
        try:
            page.set_content(render_comparison(profile, rows, zh))
            page.locator("[data-output-benchmark]").select_option("factual")
            isolated = page.locator(".compare-task").filter(
                has=page.locator("h3", has_text="Who founded isolated?")
            )
            label = "参考已隔离，不计质量" if zh else "Reference quarantined; excluded from quality"
            assert label in isolated.inner_text()
            assert isolated.locator(".compare-match").count() == 0
            assert isolated.locator('a[href="details/isolateda.html"]').count() == 1
            assert isolated.locator(".compare-result").count() == 2
            page.locator("[data-compare-relation]").select_option("all_pass")
            assert page.locator("[data-relation-tasks] [data-show-query]").all_text_contents() == [
                "Who founded genuine?"
            ]
            page.locator("[data-compare-relation]").select_option("unknown")
            assert page.locator("[data-relation-tasks] [data-show-query]").all_text_contents() == [
                "Who founded isolated?"
            ]
            page.locator("[data-compare-relation]").select_option("all_fail")
            assert page.locator("[data-relation-tasks] [data-show-query]").all_text_contents() == [
                "Who founded leaked?"
            ]
            assert page.locator("[data-output-pages] .compare-match").count() == 0
            assert "Full summary retained" in page.locator("[data-output-pages]").text_content()
            page.locator("[data-compare-relation]").select_option("all")
            page.locator("[data-compare-metric]").select_option("source")
            page.locator("[data-compare-relation]").select_option("all_pass")
            assert page.locator("[data-relation-tasks] [data-show-query]").all_text_contents() == [
                "Who founded genuine?"
            ]
            page.locator("[data-compare-metric]").select_option("availability")
            assert page.locator("[data-relation-tasks] [data-show-query]").count() == 3
        finally:
            browser.close()


def test_empty_quarantined_response_has_no_quality_verdict_but_fails_availability():
    profile, rows = scenarios()
    rows[0]["results"] = []
    out = comparison_data(profile, rows)["cases"][0]["outputs"]["a"]
    assert out["primary"] is out["source"] is out["answer"] is None
    assert out["availability"] is False
    assert out["count"] == 0
    assert out["detail"] == "details/isolateda.html"
