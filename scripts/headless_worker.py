"""One disposable browser call. stdout is a single result, stderr diagnostics."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.document_text import extract_document_text
from harness.web_fetch.providers.headless import cli_command, completion_metadata
from harness.web_fetch.safety import validate_http_url


def require_normalized_retrieval_plan(request):
    """Enforce presence at this process boundary; the runner owns validation."""
    plan = request.get('retrieval_plan')
    if request.get('track') == 'dynamic' and not plan:
        raise ValueError('dynamic tasks require a normalized retrieval_plan')
    if request.get('track') not in {None, 'dynamic'} and plan:
        raise ValueError('retrieval_plan is only valid for dynamic tasks')
    return plan


def fetch(request):
    # Match the provider boundary; this validates URL syntax, not network isolation.
    validate_http_url(request['url'])
    plan = require_normalized_retrieval_plan(request)
    engine, timeout = request['engine'], request['timeout']
    output_mode = request.get('output_mode', 'html')
    route_timeout = min(timeout, plan['timeout_ms'] / 1000) if plan else timeout
    start = time.monotonic()
    meta = {'completion': completion_metadata(engine, plan, output_mode)}
    if meta['completion'] is not None:
        meta['completion']['effective_route_timeout_ms'] = round(route_timeout * 1000)
    html = ''
    try:
        if engine == 'kitesurf' and request.get('remote_markdown_api'):
            from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider

            provider = CloudflareMarkdownProvider('kitesurf', {
                'account_id': request['account_id'],
                'api_token': request['api_token'],
                'browser': request.get('browser', 'kitesurf'),
                'wait_until': request.get('wait_until', 'networkidle2'),
                'max_bytes': request.get('max_bytes', 20_000_000),
            })
            result = provider.fetch(
                type('Case', (), {'url': request['url'], 'task_kind': request.get('task_kind', 'body')})(),
                timeout,
            )
            return {
                'ok': result.ok,
                'content': result.content,
                'html': '',
                'status_code': result.status_code,
                'final_url': result.final_url,
                'title': result.title,
                'error_type': result.error_type,
                'error_message': result.error_message,
                'metadata': result.metadata,
            }
        if engine in ('moli', 'obscura', 'lightpanda'):
            command = cli_command(
                engine,
                request['binary'],
                request['url'],
                route_timeout,
                output_mode,
                request.get('wait_until'),
                plan,
                allow_private_network=bool(request.get('allow_private_network')),
                ca_cert=request.get('ca_cert'),
            )
            environment = None
            if engine == 'obscura' and request.get('ca_cert'):
                environment = {**os.environ, 'SSL_CERT_FILE': str(request['ca_cert'])}
            result = subprocess.run(command, capture_output=True, timeout=route_timeout,
                                    **({'env': environment} if environment else {}))
            meta.update(browser_exit_code=result.returncode,
                        browser_stderr=result.stderr.decode('utf-8', 'replace')[:4096])
            if result.returncode:
                return dict(ok=False, content='', html='', error_type='browser_exit',
                            error_message=meta['browser_stderr'] or str(result.returncode), metadata=meta)
            if output_mode == 'file':
                meta['raw_stdout_sha256'] = hashlib.sha256(result.stdout).hexdigest()
                meta['raw_fetch_ms'] = round((time.monotonic()-start)*1000, 2)
                return dict(ok=bool(result.stdout), content='', html='', status_code=None,
                            final_url=None, metadata=meta,
                            files_b64=[base64.b64encode(result.stdout).decode('ascii')]
                            if result.stdout else [],
                            error_type=None if result.stdout else 'empty_response')
            if output_mode == 'document' and result.stdout.startswith((b'%PDF', b'PK')):
                raw_fetch_ms = round((time.monotonic()-start)*1000, 2)
                text, extractor = extract_document_text(result.stdout)
                meta['_raw_stdout_b64'] = base64.b64encode(result.stdout).decode('ascii')
                meta['raw_stdout_sha256'] = hashlib.sha256(result.stdout).hexdigest()
                meta['raw_fetch_ms'] = raw_fetch_ms
                meta['document_extractor'] = extractor
                meta['document_extract_ms'] = round((time.monotonic()-start)*1000-raw_fetch_ms, 2)
                return dict(ok=bool(text.strip()), content=text, html='', status_code=None,
                            final_url=None, metadata=meta,
                            error_type=None if text.strip() else 'empty_response')
            try:
                browser_output = result.stdout.decode('utf-8')
            except UnicodeDecodeError:
                meta['_raw_stdout_b64'] = base64.b64encode(result.stdout).decode('ascii')
                meta['raw_stdout_sha256'] = hashlib.sha256(result.stdout).hexdigest()
                return dict(ok=False, content='', html='', error_type='non_utf8_response',
                    error_message='HTML adapter received non-UTF-8 bytes; preserved without conversion', metadata=meta)
            if output_mode == 'markdown':
                meta['native_markdown_ms'] = round((time.monotonic()-start)*1000, 2)
                return dict(ok=bool(browser_output.strip()), content=browser_output, html='',
                            status_code=None, final_url=None, metadata=meta,
                            error_type=None if browser_output.strip() else 'empty_response')
            html = browser_output
            if output_mode == 'document':
                meta['document_response'] = 'serialized_html_not_raw_document'
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
                        args=request.get('browser_args') or [], timeout=timeout*1000)
                try:
                    meta['browser_version'] = browser.version
                    # The benchmark fixture has a per-run CA, not a system-trusted
                    # certificate. Only isolated loopback requests may bypass the
                    # browser's public trust store.
                    fixture_https = bool(request.get('ca_cert')) and request.get('network_policy') == 'loopback_only'
                    context = browser.new_context(ignore_https_errors=fixture_https)
                    page = context.new_page()
                    response = page.goto(
                        request['url'],
                        wait_until=request.get('wait_until') or 'domcontentloaded',
                        timeout=max(1, (route_timeout-(time.monotonic()-start))*1000),
                    )
                    if plan:
                        remaining_ms = max(1, min(
                            plan['timeout_ms'],
                            (route_timeout-(time.monotonic()-start))*1000,
                        ))
                        if plan['wait_kind'] == 'selector':
                            page.wait_for_selector(plan['wait_value'], timeout=remaining_ms)
                        else:
                            page.wait_for_function(plan['wait_value'], timeout=remaining_ms)
                        if plan['delay_ms']:
                            page.wait_for_timeout(plan['delay_ms'])
                    # Avoid Playwright's XMLSerializer helper, which is not
                    # implemented by every CDP engine. Same expression for both.
                    html = page.evaluate('document.documentElement.outerHTML')
                    final_url = page.url
                    status = response.status if response else None
                finally:
                    browser.close()
        meta['fetch_html_ms'] = round((time.monotonic()-start)*1000, 2)
        converter = Path(__file__).with_name('html_to_markdown.mjs')
        package = Path(__file__).resolve().parents[1] / 'node_modules/node-html-markdown/package.json'
        meta['extractor_version'] = json.loads(package.read_text())['version']
        meta['converter_sha256'] = hashlib.sha256(converter.read_bytes()).hexdigest()
        meta['node_version'] = subprocess.run(
            [shutil.which('node') or 'node', '--version'], capture_output=True, text=True,
            check=True, timeout=5).stdout.strip()
        convert_start = time.monotonic()
        converted = subprocess.run(
            [shutil.which('node') or 'node', str(converter)], input=html.encode(),
            capture_output=True, timeout=max(.001, timeout-(time.monotonic()-start)))
        if converted.returncode:
            raise RuntimeError(converted.stderr.decode('utf-8', 'replace')[:4000])
        content = converted.stdout.decode('utf-8')
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
