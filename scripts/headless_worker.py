"""One disposable browser call. stdout is a single result, stderr diagnostics."""
import json
import base64
import hashlib
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.providers.headless import cli_command


def fetch(request):
    import pyhtml2md
    engine, timeout = request['engine'], request['timeout']
    start = time.monotonic()
    meta = {'extractor_version': version('pyhtml2md')}
    html = ''
    try:
        if engine in ('moli', 'obscura', 'lightpanda'):
            command = cli_command(engine, request['binary'], request['url'], timeout)
            result = subprocess.run(command, capture_output=True, timeout=timeout)
            meta.update(browser_exit_code=result.returncode,
                        browser_stderr=result.stderr.decode('utf-8', 'replace')[:4096])
            try:
                html = result.stdout.decode('utf-8')
            except UnicodeDecodeError:
                meta['_raw_stdout_b64'] = base64.b64encode(result.stdout).decode('ascii')
                meta['raw_stdout_sha256'] = hashlib.sha256(result.stdout).hexdigest()
                return dict(ok=False, content='', html='', error_type='non_utf8_response',
                    error_message='HTML adapter received non-UTF-8 bytes; preserved without conversion', metadata=meta)
            if result.returncode:
                return dict(ok=False, content='', html=html, error_type='browser_exit',
                            error_message=meta['browser_stderr'] or str(result.returncode), metadata=meta)
            status = None
            final_url = None
        else:
            from playwright.sync_api import sync_playwright
            meta['playwright_version'] = version('playwright')
            with sync_playwright() as p:
                if engine == 'kitesurf':
                    browser = p.chromium.connect_over_cdp(request['endpoint'], timeout=timeout*1000)
                else:
                    browser = p.chromium.launch(executable_path=request['binary'], headless=True,
                        timeout=timeout*1000)
                try:
                    meta['browser_version'] = browser.version
                    context = browser.new_context()
                    page = context.new_page()
                    response = page.goto(request['url'], wait_until='domcontentloaded',
                        timeout=max(1, (timeout-(time.monotonic()-start))*1000))
                    # Avoid Playwright's XMLSerializer helper, which is not
                    # implemented by every CDP engine. Same expression for both.
                    html = page.evaluate('document.documentElement.outerHTML')
                    final_url = page.url
                    status = response.status if response else None
                finally:
                    browser.close()
        meta['fetch_html_ms'] = round((time.monotonic()-start)*1000, 2)
        convert_start = time.monotonic()
        content = pyhtml2md.convert(html)
        meta['conversion_ms'] = round((time.monotonic()-convert_start)*1000, 2)
        ok = bool(html.strip())
        return dict(ok=ok, content=content, html=html, status_code=status,
                    final_url=final_url, metadata=meta,
                    error_type=None if ok else 'empty_response')
    except Exception as error:
        return dict(ok=False, content='', html=html, metadata=meta,
                    error_type='timeout' if 'Timeout' in type(error).__name__ else 'browser_error',
                    error_message=str(error)[:4000])


if __name__ == '__main__':
    print(json.dumps(fetch(json.load(sys.stdin)), ensure_ascii=False))
