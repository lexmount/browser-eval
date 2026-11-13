"""Readers can switch output levels and filter each level independently."""

import os
from copy import deepcopy

import pytest

from harness.reporting.visual import browser_executable
from harness.web_search.comparison import render_level_results
from harness.web_search.output_levels import assessed_levels
from harness.web_search.report import SCRIPT, STYLE
from tests.web_search.test_level_scores import complete_levels


@pytest.mark.parametrize("width", [480, 1280])
def test_level_tabs_and_independent_filters_in_real_browser(tmp_path, width):
    playwright = pytest.importorskip("playwright.sync_api")
    profile, rows, admissions, artifacts, evidence = complete_levels()
    views = assessed_levels(
        profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts
    )
    for view in views.values():
        for method in view["profile"]["methods"]:
            method["label"] = "Synthetic method"
    row = rows[0]
    native = dict(row, answer="Synthetic answer")
    answer_profile = deepcopy(profile)
    answer_profile["protocol"]["relevance_level"] = "l3"
    answer_profile["methods"][0]["label"] = "Synthetic answer method"
    views["l3"] = dict(
        profile=answer_profile,
        rows=[native],
        answer_scores=[
            dict(
                case_id=row["case_id"],
                method=row["method"],
                answer_grade=2,
                citation_count=0,
                supported_citations=0,
            )
        ],
    )
    document = tmp_path / "reader.html"
    document.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<style>"
        + STYLE
        + "</style><button data-theme>Theme</button><main>"
        + render_level_results(views)
        + "</main><script>"
        + SCRIPT
        + "</script></html>"
    , encoding="utf-8")
    binary = os.environ.get("WEBSTONE_TEST_BROWSER_BINARY") or browser_executable()
    with playwright.sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=binary, headless=True)
        try:
            page = browser.new_page(viewport=dict(width=width, height=850))
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(document.as_uri())
            page.wait_for_selector('[data-level-results="l1"] .compare-task')
            assert page.locator("[data-search-comparison][data-initialized]").count() == 2
            second = page.locator('[data-level-results="l2"]')
            page.locator('[data-level-select="l2"]').click()
            assert second.is_visible()
            assert not page.locator('[data-level-results="l1"]').is_visible()
            second.locator("[data-output-query]").fill("unmatched query")
            assert second.locator(".compare-task").count() == 0
            page.locator('[data-level-select="l1"]').click()
            assert page.locator('[data-level-results="l1"] .compare-task').count() == 1
            page.locator('[data-level-select="l3"]').click()
            page.locator("[data-level-inventory-filter]").fill("unmatched answer")
            assert page.locator("[data-answer-result]:visible").count() == 0
            page.locator("[data-level-inventory-filter]").fill("")
            assert page.locator("[data-answer-result]:visible").count() == 1
            assert page.evaluate("document.documentElement.scrollWidth-innerWidth") <= 2
            assert not errors
        finally:
            browser.close()
