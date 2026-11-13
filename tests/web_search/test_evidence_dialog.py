"""Operate per-result and complete native-response evidence in a real browser."""
import json
import os

import pytest

from harness.reporting.visual import browser_executable
from harness.web_search.report import SCRIPT, STYLE, numeric_cell, table

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    try:
        binary = os.environ.get("WEBSTONE_TEST_BROWSER_BINARY") or browser_executable()
    except ValueError:
        pytest.skip("Install Chromium or set WEBSTONE_TEST_BROWSER_BINARY")
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


def test_comparison_sorting_uses_raw_numbers_keeps_missing_last_and_preserves_highlights(browser):
    page = browser.new_page()
    rows = [
        '<tr><td>Zulu</td>' + numeric_cell('★ 10/20 · 50%', 0.5, 'quality', 'max', True) + '</tr>',
        '<tr><td>Alpha</td>' + numeric_cell('2/20 · 10%', 0.1, 'quality', 'max', False) + '</tr>',
        '<tr><td>Beta</td>' + numeric_cell('2/20 · 10%', 0.1, 'quality', 'max', False) + '</tr>',
        '<tr><td>Missing</td>' + numeric_cell('—', None, 'quality', 'max', False) + '</tr>',
    ]
    try:
        page.set_content('<html lang="en"><style>' + STYLE + '</style><button data-theme>Theme</button>'
                         + table(['Method', 'Hit@5'], rows) + '</html>')
        page.add_script_tag(content=SCRIPT)
        assert page.locator('tbody td').evaluate_all(
            "cells => cells.every(cell => getComputedStyle(cell).display === 'table-cell')")
        def names():
            return page.locator('tbody tr td:first-child').all_text_contents()
        page.get_by_role('button', name='Hit@5', exact=True).click()
        assert names() == ['Alpha', 'Beta', 'Zulu', 'Missing']
        assert page.locator('th').nth(1).get_attribute('aria-sort') == 'ascending'
        page.get_by_role('button', name='Hit@5', exact=True).press('Enter')
        assert names() == ['Zulu', 'Alpha', 'Beta', 'Missing']
        winner = page.locator('.comparison-winner').locator('xpath=ancestor::tr')
        assert winner.inner_text().startswith('Zulu')
        page.get_by_role('button', name='Method', exact=True).click()
        assert names() == ['Alpha', 'Beta', 'Missing', 'Zulu']
        page.get_by_role('button', name='Method', exact=True).click()
        assert names() == ['Zulu', 'Missing', 'Beta', 'Alpha']
    finally:
        page.close()


def test_method_names_sort_in_standard_overview_and_interpretation(tmp_path, browser):
    from harness.reporting.visual import local_report_server
    from harness.web_search.example import example

    example(tmp_path)
    with local_report_server(tmp_path / 'report') as origin:
        page = browser.new_page()
        try:
            for route in ('index.html', 'interpretation.html'):
                page.goto(origin + '/' + route + '?report-language=zh')
                comparison = page.locator('main table').first
                button = comparison.locator('th button').first
                button.click()
                assert comparison.locator('tbody tr td:first-child').all_text_contents() == [
                    'Fixture A', 'Fixture B']
                button.click()
                assert comparison.locator('tbody tr td:first-child').all_text_contents() == [
                    'Fixture B', 'Fixture A']
        finally:
            page.close()


def test_large_auxiliary_json_remains_readable_and_downloads_exact_bytes(tmp_path, browser):
    import base64
    import gzip

    from harness.record_io import bytes_sha256
    from harness.reporting.visual import local_report_server
    from harness.web_search.delivery import export, verify
    from harness.web_search.example import example
    from harness.web_search.report import build

    example(tmp_path)
    raw = (json.dumps({'text': 'café source ' * 120000}, ensure_ascii=False) + '\r\n').encode()
    (tmp_path / 'large.json').write_bytes(raw)
    content = {'assets': [{'path': 'report-assets/large.json', 'source': 'large.json',
                           'sha256': bytes_sha256(raw)}], 'sections': [{
        'page': 'method', 'title': {'en': 'Synthetic archive', 'zh': 'Synthetic archive'},
        'sources': [{'label': {'en': 'Open synthetic archive', 'zh': 'Open synthetic archive'},
                     'url': 'report-assets/large.json'}]}]}
    (tmp_path / 'content.json').write_text(json.dumps(content), encoding='utf-8')
    source, delivery = tmp_path / 'report', tmp_path / 'delivery'
    build(source, source=tmp_path / 'fixture', content=tmp_path / 'content.json', include_english=True)
    export(source, delivery)
    assert verify(source, delivery)
    assert not (delivery / 'report-assets/large.json').exists()
    index = json.loads((delivery / 'delivery-index.json').read_bytes())
    shard = delivery / index['files']['report-assets/large.json']
    with local_report_server(delivery) as origin:
        page = browser.new_page(accept_downloads=True)
        try:
            for language in ('', 'en/'):
                page.goto(origin + '/' + language + 'research.html')
                link = page.get_by_role('link', name='Open synthetic archive', exact=True)
                assert link.get_attribute('href') == 'evidence.html#report-assets%2Flarge.json'
                fetched = page.evaluate("""async () => {
                    const bytes=await (await fetch('report-assets/large.json')).arrayBuffer();
                    const hash=await crypto.subtle.digest('SHA-256',bytes);
                    return [...new Uint8Array(hash)].map(x=>x.toString(16).padStart(2,'0')).join('');
                }""")
                assert fetched == bytes_sha256(raw)
                link.click()
                page.locator('[data-evidence-download]').wait_for()
                with page.expect_download() as pending:
                    page.locator('[data-evidence-download]').click()
                from pathlib import Path
                assert Path(pending.value.path()).read_bytes() == raw
        finally:
            page.close()
    values = json.loads(gzip.decompress(shard.read_bytes()))
    values['report-assets/large.json']['base64'] = base64.b64encode(b'changed').decode()
    shard.write_bytes(gzip.compress(json.dumps(values).encode(), mtime=0))
    with pytest.raises(ValueError, match='Packed evidence changed'):
        verify(source, delivery)


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


@pytest.mark.parametrize('primary', ['zh', 'en'])
def test_exported_detail_and_raw_evidence_stay_on_selected_route(tmp_path, browser, primary):
    from urllib.parse import quote, urlsplit
    from harness.reporting.visual import local_report_server
    from harness.web_search.example import example
    from harness.web_search.report import build
    from harness.web_search.delivery import export

    example(tmp_path)
    source = tmp_path / 'report'
    build(source, source=tmp_path / 'fixture', language=primary, include_english=True)
    export(source, tmp_path / 'delivery')
    manifest = json.loads((source / 'manifest.json').read_text(encoding="utf-8"))
    with local_report_server(tmp_path / 'delivery') as origin:
        page = browser.new_page()
        try:
            for lang, prefix in [('zh', ''), ('en', 'en/')]:
                for row in [manifest['rows'][0], manifest['rows'][-1]]:
                    target = prefix + 'details/' + row['id'] + '.html'
                    page.goto(origin + '/detail.html#' + quote(target, safe=''))
                    page.locator('main h1').wait_for()
                    assert urlsplit(page.url).path == '/detail.html'
                    assert page.locator('html').get_attribute('lang') == lang
                    page.locator('[data-evidence]:not([data-result])').first.click()
                    page.wait_for_function("document.querySelector('dialog pre').textContent !== '…'")
                    assert json.loads(page.locator('dialog pre').inner_text()) == json.loads((source / row['evidence']['path']).read_text(encoding="utf-8"))
                    page.keyboard.press('Escape')
                    assert not page.locator('dialog').is_visible()
                    page.goto(origin + '/evidence.html#' + quote(row['evidence']['path'], safe=''))
                    page.locator('[data-evidence-download]').wait_for()
                    assert urlsplit(page.url).path == '/evidence.html'
                    assert json.loads(page.locator('[data-delivery-target]').inner_text()) == json.loads((source / row['evidence']['path']).read_text(encoding="utf-8"))
        finally:
            page.close()
