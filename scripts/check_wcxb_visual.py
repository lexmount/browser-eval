"""Full bilingual, theme and viewport gate for the current WCXB report."""
from __future__ import annotations

import argparse
import json
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from playwright.sync_api import sync_playwright


def binary_from_env(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith('CHROMIUM_BINARY='):
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise ValueError('CHROMIUM_BINARY is missing from the local benchmark .env')


def check(root: Path, browser_binary: str) -> None:
    root = root.resolve()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checked = 0
    try:
        score_rows = [json.loads(line) for line in (root / 'wcxb-scores.jsonl').read_text().splitlines() if line]
        providers = list(dict.fromkeys(row['provider'] for row in score_rows))
        if len(providers) < 2:
            raise ValueError('WCXB visual comparison requires at least two providers')
        passing_row = next((row for row in score_rows if row['quality_eligible'] and row['diagnosis'] == 'none'), None)
        diagnostic_row = next((row for row in score_rows if row['quality_eligible'] and row['diagnosis'] == 'low_precision'), None)
        if passing_row is None or diagnostic_row is None:
            raise ValueError('WCXB visual gate requires an unlocated result and a low-precision result')
        case_id = passing_row['case_id']
        diagnostic_id = diagnostic_row['case_id']
        qualified_count = len({row['case_id'] for row in score_rows if row['quality_eligible']})
        provider_count = len(providers)
        routes = ('index.html', 'datasets/index.html', 'analysis/index.html',
                  'methodology/index.html', 'review/index.html', 'guide/index.html',
                  'adoption/index.html', 'reproduce/index.html', 'changes/index.html',
                  'accounts/index.html', f'cases/{case_id}.html', f'cases/{diagnostic_id}.html')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=browser_binary)
            try:
                for lang in ('zh', 'en'):
                    for route in routes:
                        for width in (390, 1365):
                            for dark in (False, True):
                                page = browser.new_page(viewport={'width': width, 'height': 844})
                                try:
                                    errors = []
                                    page.on('pageerror', lambda error: errors.append(str(error)))
                                    prefix = 'en/' if lang == 'en' else ''
                                    page.goto(f'http://127.0.0.1:{server.server_port}/{prefix}{route}',
                                              wait_until='networkidle')
                                    if not page.locator('link[rel="stylesheet"]').evaluate('(node) => Boolean(node.sheet)'):
                                        raise ValueError(f'Report stylesheet did not load: {lang} {route}')
                                    if dark:
                                        page.locator('header button').click()
                                    if page.locator('body').evaluate('(node) => node.scrollWidth') > width:
                                        raise ValueError(f'Horizontal page overflow: {lang} {route} {width} {dark}')
                                    if errors:
                                        raise ValueError(f'Browser error: {lang} {route}: {errors}')
                                    if route == 'index.html' and not dark:
                                        if page.locator('#wcxb-core .comparison-winner').count() < 4:
                                            raise ValueError('Core comparison is missing column winners')
                                        quality = [float(cell.get_attribute('data-sort')) for cell in page.locator('#wcxb-core tbody td:nth-child(5)').all()]
                                        if float(page.locator('#wcxb-core tbody tr').first.locator('td').nth(4).get_attribute('data-sort')) != max(quality):
                                            raise ValueError('Core table is not ordered by qualified F1')
                                        page.locator('[data-core-sort="5"]').click()
                                        latency = [float(cell.get_attribute('data-sort')) for cell in page.locator('#wcxb-core tbody td:nth-child(6)').all()]
                                        if float(page.locator('#wcxb-core tbody tr').first.locator('td').nth(5).get_attribute('data-sort')) != min(latency):
                                            raise ValueError('Core latency sorting did not work')
                                        page.locator('#home-type').select_option('article')
                                        if page.locator('#wcxb-types tbody tr:visible').count() != 1:
                                            raise ValueError('Page-type filter does not narrow the comparison')
                                    if route == 'datasets/index.html' and not dark:
                                        if page.locator('#wcxb-dataset tbody tr:visible').count() != qualified_count:
                                            raise ValueError('Dataset does not show every page result')
                                        page.locator('#dataset-query').fill(case_id)
                                        if page.locator('#dataset-count').inner_text() != '1':
                                            raise ValueError('Dataset page search failed')
                                        if page.locator('#wcxb-dataset').locator('..').evaluate('(node) => node.getBoundingClientRect().height') > 180:
                                            raise ValueError('Filtered dataset leaves a large empty table')
                                        preview_href = page.locator('#wcxb-dataset tbody tr:visible a[href$="#preview"]').get_attribute('href')
                                        if f'cases/{case_id}.html#preview' not in (preview_href or ''):
                                            raise ValueError('Dataset preview does not target its page')
                                    if route == 'analysis/index.html' and not dark:
                                        if int(page.locator('#count').inner_text()) == 0:
                                            raise ValueError('Default results filter is empty')
                                        page.locator('#scope').select_option('all')
                                        if page.locator('#count').inner_text() != str(qualified_count * provider_count):
                                            raise ValueError('Results filter does not expose the qualified matrix')
                                        page.locator('#q').fill(case_id)
                                        if page.locator('#count').inner_text() != str(provider_count):
                                            raise ValueError('Same-case search does not expose five results')
                                        page.locator('#compare-baseline').select_option(providers[0])
                                        page.locator(f'[name="compare-provider"][value="{providers[1]}"]').check()
                                        if page.locator('#comparison-rows tr').count() != qualified_count:
                                            raise ValueError('Same-page comparison does not cover every qualified page')
                                        page.locator('#compare-query').fill(case_id)
                                        if page.locator('#compare-count').inner_text() != '1':
                                            raise ValueError('Same-page comparison search failed')
                                        page.locator('tr[data-provider] button[data-diagnosis-open]:visible').first.click()
                                        if not page.locator('#diagnosis-dialog').evaluate('(node) => node.open'):
                                            raise ValueError('Inventory diagnosis did not open')
                                        detail_href = page.locator('#diagnosis-dialog [data-diagnosis-link]').get_attribute('href')
                                        if not detail_href or f'cases/{case_id}.html' not in detail_href:
                                            raise ValueError('Inventory diagnosis does not target the matching case')
                                        trigger = page.locator('tr[data-provider] button[data-diagnosis-open]:visible').first
                                        page.keyboard.press('Escape')
                                        if page.locator('#diagnosis-dialog').evaluate('(node) => node.open'):
                                            raise ValueError('Escape did not close the diagnosis dialog')
                                        if not trigger.evaluate('(node) => document.activeElement === node'):
                                            raise ValueError('Diagnosis focus did not return to its trigger')
                                        if lang == 'zh' and width == 1365:
                                            page.locator('tr[data-provider] a:visible').first.click()
                                            if f'cases/{case_id}.html' not in page.url:
                                                raise ValueError('Nested result link does not open the case')
                                            page.locator('header a').first.click()
                                            if not page.url.endswith('/index.html'):
                                                raise ValueError('Nested result cannot return to the report root')
                                    if route.startswith('cases/') and not dark:
                                        preview = page.frame_locator('iframe.frozen-preview').locator('body')
                                        if not preview.inner_text().strip():
                                            raise ValueError('Frozen page preview is blank')
                                        if route.endswith(f'/{diagnostic_id}.html'):
                                            locator = page.locator(f'#{diagnostic_row["provider"]} .tag').inner_text()
                                            if locator != ('精确率低' if lang == 'zh' else 'Low precision'):
                                                raise ValueError('Diagnostic case lacks its locator evidence')
                                        page.locator('details[data-output]:has(pre[data-provider])').first.click()
                                        page.wait_for_function("() => document.querySelector('pre[data-provider]')?.textContent.length > 0")
                                        if 'Unable to load evidence' in page.locator('pre[data-provider]').first.inner_text():
                                            raise ValueError('Case evidence did not load')
                                    checked += 1
                                finally:
                                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(f'PASS: {checked} bilingual WCXB route, width and theme combinations')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--browser-binary')
    args = parser.parse_args()
    check(args.report, args.browser_binary or binary_from_env(Path('.env')))
