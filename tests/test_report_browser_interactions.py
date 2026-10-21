"""Real browser checks of maintained report controls on small local fixtures.

One browser/viewport; no provider calls, published report, or screenshot matrix.
"""
import http.server
import json
import os
import threading
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright

from harness.reporting.visual import browser_executable, local_report_server


@pytest.fixture(scope="module")
def browser():
    binary = os.environ.get("BROWSER_EVAL_TEST_BROWSER_BINARY")
    if not binary:
        try:
            binary = browser_executable()
        except ValueError:
            pytest.skip("Install Chromium or set BROWSER_EVAL_TEST_BROWSER_BINARY")
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=binary, headless=True)
        yield instance
        instance.close()


def test_browser_use_sort_filter_restore_and_keyboard_dialog(tmp_path, browser):
    from harness.browser_use.report import REPORT_JS

    (tmp_path / "app.js").write_text(REPORT_JS)
    (tmp_path / "index.html").write_text('''<!doctype html><html><head><meta charset="utf-8">
    <script defer src="app.js"></script></head><body>
    <input data-search aria-label="Search"><select data-filter="result">
    <option value="">All</option><option value="fail">Fail</option></select>
    <span data-visible-count></span><table id="tasks"><thead><tr>
    <th><button onclick="sortTable('tasks',0)">Time</button></th></tr></thead><tbody>
    <tr data-tier="easy" data-result="pass"><td data-sort="20">20 ms</td></tr>
    <tr data-tier="hard" data-result="fail"><td data-sort="3">3 ms error</td></tr>
    <tr data-tier="easy" data-result="pass"><td>—</td></tr>
    </tbody></table><button id="open" onclick="openDiagnosis('detail')">Evidence</button>
    <dialog id="detail" aria-label="Evidence"><button onclick="closeDiagnosis('detail')">Close</button>
    <pre>Exact evaluator error</pre></dialog></body></html>''')
    with local_report_server(tmp_path) as origin:
        page = browser.new_page()
        try:
            page.goto(origin)
            page.locator('th button').click()
            assert page.locator('tbody td').all_text_contents() == ['3 ms error', '20 ms', '—']
            page.locator('th button').click()
            assert page.locator('tbody td').all_text_contents() == ['20 ms', '3 ms error', '—']
            assert page.locator('th').get_attribute('aria-sort') == 'descending'
            page.locator('[data-filter]').select_option('fail')
            assert page.locator('tbody tr:visible').count() == 1
            page.reload()
            assert page.locator('[data-filter]').input_value() == 'fail'
            assert page.locator('tbody tr:visible').count() == 1
            page.locator('#open').click()
            assert page.locator('dialog').is_visible()
            assert 'Exact evaluator error' in page.locator('dialog').inner_text()
            page.keyboard.press('Escape')
            assert not page.locator('dialog').is_visible()
            assert page.locator('#open').evaluate('(e)=>e===document.activeElement')
        finally:
            page.close()


@contextmanager
def hosted_preview(root):
    from harness.trajectory_replay.webarena_report_visual import _QuietHandler

    def handler(*args, **kwargs):
        return _QuietHandler(*args, directory=str(root), **kwargs)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    server.preview_errors = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/api/preview/replay~fixture/'
        assert server.preview_errors == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_replay_real_assets_evidence_dialog_and_private_nested_navigation(tmp_path, browser):
    from harness.trajectory_replay.webarena_report import _assets, _shell

    _assets(tmp_path)
    body = '''<input data-filter="q"><span data-result-count></span>
    <div data-results><table><tbody><tr data-key="fixture" data-task="1">
    <td><button data-open-diagnosis>Diagnose</button></td></tr></tbody></table></div>
    <dialog id="diagnosis-dialog"><div class="dialog-head"><button>Close</button></div>
    <strong data-dialog-title></strong><span data-dialog-stage></span><span data-dialog-step></span>
    <span data-dialog-category></span><pre data-dialog-error></pre><p data-dialog-cause></p>
    <p data-dialog-remediation></p><a data-dialog-link>Detail</a><a data-dialog-step-link>Step</a></dialog>'''
    (tmp_path / 'index.html').write_text(_shell('zh', 'index', 'Home', '<h1>Home</h1>'))
    (tmp_path / 'results.html').write_text(_shell('zh', 'results', 'Results', body))
    (tmp_path / 'result').mkdir()
    (tmp_path / 'result/fixture.html').write_text(_shell('zh', 'results', 'Detail', '<h1>Detail</h1>', depth=1, current_route='result/fixture.html'))
    (tmp_path / 'data/evidence').mkdir(parents=True)
    (tmp_path / 'data/results.json').write_text(json.dumps([{'key':'fixture','browser':'Synthetic','task_id':1,'failure_stage':'official_evaluation'}]))
    (tmp_path / 'data/evidence/fixture.json').write_text(json.dumps({'official_evaluation':{'error':'Expected mutation was absent'}}))
    with hosted_preview(tmp_path) as origin:
        page = browser.new_page()
        try:
            page.goto(origin + 'results.html')
            page.wait_for_selector('[data-results][data-results-ready=true]')
            page.locator('[data-open-diagnosis]').click()
            page.wait_for_selector('dialog[data-loaded=true]')
            assert 'Expected mutation was absent' in page.locator('[data-dialog-error]').inner_text()
            page.keyboard.press('Escape')
            assert not page.locator('dialog').is_visible()
            assert page.locator('[data-open-diagnosis]').evaluate('(e)=>e===document.activeElement')
            page.goto(origin + 'result/fixture.html')
            assert page.locator('link[data-report-css]').evaluate('(e)=>Boolean(e.sheet)')
            page.locator('nav a[href="../index.html"]').click()
            assert page.url == origin + 'index.html'
            assert page.locator('h1').inner_text() == 'Home'
        finally:
            page.close()


def test_wcxb_generated_filter_and_diagnosis_keyboard_return(tmp_path, browser):
    from harness.web_fetch.report import generate_wcxb
    from tests.wcxb_fixture import create_report_inputs

    inputs = create_report_inputs(tmp_path)
    root = inputs[0]
    generate_wcxb(*inputs)
    with local_report_server(root) as origin:
        page = browser.new_page()
        try:
            page.goto(origin + '/analysis/index.html')
            page.locator('#scope').select_option('all')
            rows = page.locator('tr[data-provider]:visible')
            assert rows.count() > 0
            page.locator('#q').fill('impossible-fixture-query')
            assert rows.count() == 0
            page.locator('#q').fill('')
            assert rows.count() > 0
            trigger = page.locator('[data-diagnosis-open]:visible').first
            trigger.click()
            assert page.locator('#diagnosis-dialog').is_visible()
            assert page.locator('[data-diagnosis-error]').inner_text()
            page.keyboard.press('Escape')
            assert not page.locator('#diagnosis-dialog').is_visible()
            assert trigger.evaluate('(e)=>e===document.activeElement')
        finally:
            page.close()


def test_moli_actual_evidence_loader_search_and_keyboard_return(tmp_path, browser):
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / 'scripts/moli_report'
    html = (source / 'index.html').read_text().replace('{{lang}}', 'zh').replace('{{base}}', '')
    html = html.replace('<script defer src="mermaid.min.js"></script>', '')
    html = html.replace('<link rel="stylesheet" href="style.css" />', '')
    html = html.replace('</body>', '<button id="fixture-evidence" data-evidence="data/log.txt">Open log</button></body>')
    (tmp_path / 'index.html').write_text(html)
    (tmp_path / 'app.js').write_text((source / 'app.js').read_text())
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/model.json').write_text(json.dumps({'cases': [], 'functional_methods': []}))
    (tmp_path / 'data/log.txt').write_text('Exact fixture error\nExpected mutation missing')
    with local_report_server(tmp_path) as origin:
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + '/#page=method')
            page.wait_for_selector('main[data-route="method"]')
            page.locator('#fixture-evidence').click()
            page.wait_for_selector('#evidence-text')
            assert 'Expected mutation missing' in page.locator('#evidence-text').inner_text()
            page.locator('#evidence-query').fill('mutation')
            assert page.locator('mark.evidence-match').inner_text() == 'mutation'
            page.locator('#dialog-close').focus()
            page.keyboard.press('Escape')
            assert not page.locator('#evidence-dialog').is_visible()
            assert page.locator('#fixture-evidence').evaluate('(e)=>e===document.activeElement')
            assert errors == []
        finally:
            page.close()
