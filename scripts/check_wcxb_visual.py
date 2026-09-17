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
        def translate_path(self, path):
            return str(root / path.split('?', 1)[0].lstrip('/'))

        def do_GET(self):
            if self.path.split('?', 1)[0].endswith('.html'):
                source = Path(self.translate_path(self.path))
                if not source.is_file():
                    self.send_error(404)
                    return
                data = source.read_text().replace(
                    '<head>', f'<head><base href="http://127.0.0.1:{self.server.server_port}/">', 1
                ).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                super().do_GET()

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checked = 0
    try:
        case_id = json.loads((root / 'sample-manifest.json').read_text())['cases'][0]['file_id']
        routes = ('index.html', 'datasets/index.html', 'analysis/index.html',
                  'methodology/index.html', 'review/index.html', 'guide/index.html',
                  'adoption/index.html', 'reproduce/index.html', 'changes/index.html',
                  'accounts/index.html', f'cases/{case_id}.html')
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
                                    if dark:
                                        page.locator('header button').click()
                                    if page.locator('body').evaluate('(node) => node.scrollWidth') > width:
                                        raise ValueError(f'Horizontal page overflow: {lang} {route} {width} {dark}')
                                    if errors:
                                        raise ValueError(f'Browser error: {lang} {route}: {errors}')
                                    if route == 'analysis/index.html' and not dark:
                                        page.locator('#scope').select_option('all')
                                        if page.locator('#count').inner_text() != '2555':
                                            raise ValueError('Results filter does not expose the full matrix')
                                        page.locator('#q').fill(case_id)
                                        if page.locator('#count').inner_text() != '5':
                                            raise ValueError('Same-case search does not expose five results')
                                    if route.startswith('cases/') and not dark:
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
