"""Operate per-result and complete native-response evidence in a real browser."""
import json
import os

import pytest

from harness.reporting.visual import browser_executable
from harness.web_search.report import SCRIPT

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    try:
        binary = os.environ.get("BROWSER_EVAL_TEST_BROWSER_BINARY") or browser_executable()
    except ValueError:
        pytest.skip("Install Chromium or set BROWSER_EVAL_TEST_BROWSER_BINARY")
    with playwright.sync_playwright() as engine:
        instance = engine.chromium.launch(executable_path=binary, headless=True)
        yield instance
        instance.close()


def dialog_page(browser, raw, index="1"):
    page = browser.new_page()
    page.route("https://evidence.test/call", lambda route: route.fulfill(
        status=200, content_type="application/json", body=raw))
    page.set_content('''<html lang="en"><body><button data-theme>Theme</button>
        <button id="selected" data-evidence="https://evidence.test/call">
        Summary and content</button>
        <button id="original" data-evidence="https://evidence.test/call">Open response</button>
        <dialog><button data-close>Close</button><pre></pre><a hidden>Full result</a></dialog>
        </body></html>''')
    page.locator("#selected").evaluate("(button, index) => button.dataset.result = index", index)
    page.add_script_tag(content=SCRIPT)
    return page


def open_evidence(page, selector):
    page.locator(selector).click()
    page.wait_for_function("document.querySelector('dialog pre').textContent !== '…'")
    return page.locator("dialog pre").inner_text()


@pytest.mark.parametrize("adapter", ["lexmount", "deepseek", "bocha"])
def test_selected_native_item_and_complete_response_are_distinct(browser, adapter):
    items = [{"url": "https://example.com/first", "title": "Other rank", "summary": "UNSELECTED"},
             {"url": "https://example.com/second", "title": "Chosen rank", "summary": "SELECTED"}]
    if adapter == "bocha":
        items = [{"url": item["url"], "name": item["title"], "summary": item["summary"]}
                 for item in items]
        payload = {"code": 200, "data": {"webPages": {"value": items}}, "trace": "kept"}
    else:
        items[1]["content"] = "Selected complete body" if adapter == "deepseek" else "Lexmount body"
        payload = {"results": items, "usage": {"requests": 1}, "trace": "kept"}
    page = dialog_page(browser, json.dumps(payload))
    try:
        selected = open_evidence(page, "#selected")
        assert json.loads(selected) == items[1]
        assert "UNSELECTED" not in selected
        page.keyboard.press("Escape")
        assert not page.locator("dialog").is_visible()
        assert json.loads(open_evidence(page, "#original")) == payload
    finally:
        page.close()


@pytest.mark.parametrize("payload,index", [
    ({"results": []}, "0"),
    ({"data": {"webPages": {"value": []}}}, "0"),
    ({"results": [None]}, "0"),
    ({"results": [{}]}, "0"),
    ({"results": [[]]}, "0"),
    ({"results": "not an array"}, "0"),
    ({"results": [{"url": "https://example.com"}]}, "1"),
    ({"results": [{"url": "https://example.com"}]}, "-1"),
    ({"results": [{"url": "https://example.com"}]}, ""),
    ({"results": [{"url": "https://example.com"}]}, "NaN"),
    ({"message": "upstream failed"}, "0"),
])
def test_missing_or_invalid_selected_item_is_an_explicit_error(browser, payload, index):
    page = dialog_page(browser, json.dumps(payload), index)
    try:
        selected = open_evidence(page, "#selected")
        assert selected.startswith("Error: Selected result is missing or invalid")
        page.locator("[data-close]").click()
        assert json.loads(open_evidence(page, "#original")) == payload
    finally:
        page.close()


def test_original_non_json_response_is_preserved(browser):
    raw = "upstream error\r\nnot JSON"
    page = dialog_page(browser, raw)
    try:
        assert open_evidence(page, "#original") == raw
    finally:
        page.close()
