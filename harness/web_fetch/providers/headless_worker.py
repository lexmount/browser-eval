"""One disposable browser call. stdout is a single result, stderr diagnostics."""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from importlib.metadata import version
from pathlib import Path

# Direct script execution prepends this directory to sys.path.  Leaving it
# there makes providers/http.py shadow Python's stdlib http package when the
# contract verifier imports urllib inside the isolated worker.
_WORKER_DIRECTORY = Path(__file__).resolve().parent
if sys.path and Path(sys.path[0]).resolve() == _WORKER_DIRECTORY:
    sys.path.pop(0)
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from harness.web_fetch.markdown import CONVERTER, converter_command, require_converter
from harness.web_fetch.document_text import extract_document_text
from harness.web_fetch.providers.headless import cli_command, completion_metadata, native_cdp_browser
from lexbench_webfetch.paths import validate_http_url


def browser_error_type(error):
    """Normalize known main-navigation failures without retrying security errors."""
    if 'Timeout' in type(error).__name__:
        return 'timeout'
    match = re.match(r'^(?:Page|Frame)\.goto: net::(ERR_[A-Z_]+)\b', str(error))
    if match:
        code = match.group(1)
        if code in {'ERR_NAME_NOT_RESOLVED', 'ERR_NAME_RESOLUTION_FAILED', 'ERR_DNS_TIMED_OUT'}:
            return 'dns'
        if code in {'ERR_TIMED_OUT', 'ERR_CONNECTION_TIMED_OUT'}:
            return 'timeout'
        if code in {
            'ERR_CONNECTION_CLOSED', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_REFUSED',
            'ERR_CONNECTION_ABORTED', 'ERR_ADDRESS_UNREACHABLE', 'ERR_INTERNET_DISCONNECTED',
            'ERR_NETWORK_CHANGED', 'ERR_EMPTY_RESPONSE',
        }:
            return 'network'
    return 'browser_error'


def require_normalized_retrieval_plan(request):
    """Keep the declared family while validating explicit native completion."""
    from harness.web_fetch.retrieval_hints import native_contract_for_case, parse_retrieval_hints
    plan = request.get('retrieval_plan')
    track = request.get('track')
    hints = request.get('retrieval_hints')
    case_id = request.get('case_id')
    if isinstance(hints, dict) and hints.get('contract'):
        if not isinstance(case_id, str) or not case_id:
            raise ValueError('named native retrieval contracts require case_id')
    identifier = case_id if isinstance(case_id, str) and case_id else '<unknown>'
    declared = parse_retrieval_hints(track, hints, identifier=identifier) if hints else None
    native_contract = native_contract_for_case(
        {**request, 'id': identifier}
    ) if hints else None
    if track == 'dynamic' and not plan:
        if not hints or declared is not None:
            raise ValueError('dynamic tasks require a normalized retrieval_plan or explicit native contract')
    if declared is None and hints and plan and not (
        native_contract is not None and native_contract.selector is not None
    ):
        raise ValueError('native completion cannot carry a retrieval_plan')
    if track not in {None, 'dynamic'} and plan:
        raise ValueError('retrieval_plan is only valid for dynamic tasks')
    return plan


def _native_browser_file(page, request, route_timeout, start, meta):
    """Deliver bytes from this browser navigation or its native download."""
    from playwright.sync_api import Error
    downloads = []
    page.on('download', lambda download: downloads.append(download))
    response = None
    remaining_ms = lambda: max(1, (route_timeout - (time.monotonic() - start)) * 1000)
    meta['navigation_wait_until'] = 'commit'
    try:
        response = page.goto(request['url'], wait_until='commit', timeout=remaining_ms())
    except Error as error:
        if 'ERR_ABORTED' not in str(error) and 'Download is starting' not in str(error):
            raise
        if not downloads:
            downloads.append(page.wait_for_event('download', timeout=remaining_ms()))
    if downloads:
        download = downloads[0]
        if request.get('engine') == 'moli':
            # This is a native attachment event, not an HTTP/scoring retry.
            # Moli's CLI exposes raw attachments while its current CDP download
            # completion event is incompatible with Playwright's file helper.
            command = cli_command('moli', request['binary'], download.url,
                remaining_ms()/1000, output_mode='file', ca_cert=request.get('ca_cert'),
                http_proxy=request.get('http_proxy'))
            if request.get('block_private_networks'): command.append('--block-private-networks')
            from harness.web_fetch.network_dns import native_process_dns_environment
            attachment_dns = {}
            meta['native_attachment_dns'] = attachment_dns
            with native_process_dns_environment(
                request.get('dns_mode', 'system'), request.get('native_dns_library'),
                start + route_timeout, attachment_dns,
            ) as process_environment:
                result = subprocess.run(command, capture_output=True,
                    timeout=remaining_ms()/1000, env=process_environment)
            if result.returncode: raise ValueError(result.stderr.decode('utf-8','replace'))
            raw = result.stdout
            meta.update(native_attachment_interface='moli_cli_raw_response',
                        native_navigation_count=1, native_attachment_fetch_count=1,
                        native_attachment_stderr=result.stderr.decode('utf-8','replace'))
        else:
            failure = download.failure()
            if failure:
                raise ValueError('Browser download failed: ' + failure)
            raw = Path(download.path()).read_bytes()
        final_url, status, mime = download.url, None, None
        meta['native_file_route'] = 'moli_cli_attachment_response' if request.get('engine') == 'moli' else 'browser_download'
    elif response is not None:
        final_url, status = response.url, response.status
        mime = response.headers.get('content-type')
        meta.update(file_response_status=status, file_response_url=final_url)
        raw = response.body()
        meta['native_file_route'] = 'navigation_response_body'
    else:
        raise ValueError('Browser navigation exposed neither a response nor a download')
    limit = request.get('max_original_file_bytes')
    if limit is not None and len(raw) > limit:
        meta.update(original_file_bytes=len(raw), original_file_byte_limit=limit)
        return dict(ok=False, content='', html='', status_code=status, final_url=final_url,
                    metadata=meta, error_type='output_too_large',
                    error_message='Original file exceeds the configured byte budget')
    meta.update(raw_stdout_sha256=hashlib.sha256(raw).hexdigest(),
                raw_fetch_ms=round((time.monotonic() - start) * 1000, 2),
                file_response_mime=mime)
    ok = bool(raw) and (status is None or 200 <= status < 300)
    if not ok:
        meta['_raw_stdout_b64'] = base64.b64encode(raw).decode('ascii')
    return dict(ok=ok, content='', html='', status_code=status, final_url=final_url,
                metadata=meta, files_b64=[base64.b64encode(raw).decode('ascii')] if ok else [],
                error_type=None if ok else 'http_error' if status is not None and status >= 400 else 'empty_response')


def _projection_expression(output_mode, track, detail_links=False):
    from harness.web_fetch.task_outputs import MEDIA_METADATA_EXPRESSION, DOCUMENT_PAGE_EXPRESSION, resource_expression
    return resource_expression(track, detail_links) if output_mode == 'resource' else {'media_metadata': MEDIA_METADATA_EXPRESSION,
            'document_page': DOCUMENT_PAGE_EXPRESSION}[output_mode]


def _projection_text(content, output_mode, meta, deadline):
    if output_mode != 'document_page':
        return content
    extract_start = time.monotonic()
    envelope = json.loads(content)
    if not isinstance(envelope, dict):
        raise ValueError('Invalid document projection')
    if envelope.get('source'):
        validate_http_url(envelope['source'])
        meta['embedded_document_url'] = envelope['source']
    if envelope.get('kind') in {'text', 'page_text'}:
        text = str(envelope.get('text') or '')
        meta['document_extractor'] = 'pdfjs_runtime' if envelope['kind'] == 'text' else 'rendered_page_text'
    else:
        raise ValueError('Unknown document projection kind')
    meta['document_extract_ms'] = round((time.monotonic()-extract_start)*1000, 2)
    if time.monotonic() >= deadline:
        raise TimeoutError('Document projection exceeded the call deadline')
    return text


def _evaluate_document(page, expression, deadline, meta):
    """Recover one DOM read interrupted by a navigation in this same page."""
    from playwright.sync_api import Error

    def remaining_ms():
        remaining = (deadline - time.monotonic()) * 1000
        if remaining <= 0:
            raise TimeoutError('Document read exceeded the call deadline')
        return remaining

    remaining_ms()
    try:
        value = page.evaluate(expression)
    except Error as error:
        if 'Execution context was destroyed' not in str(error):
            raise
        # A client redirect can replace the document after goto completed.
        # Reuse its navigation; never issue another request or reset the budget.
        page.wait_for_load_state('domcontentloaded', timeout=remaining_ms())
        remaining_ms()
        value = page.evaluate(expression)
        meta['document_read_recovery'] = {
            'cause': 'navigation_replaced_execution_context',
            'additional_navigations': 0,
            'additional_dom_reads': 1,
        }
    remaining_ms()
    return value


def _page_projection(page, request, output_mode, meta, deadline):
    if output_mode == 'document_page':
        from harness.web_fetch.task_outputs import DOCUMENT_PAGE_READY
        page.wait_for_function(DOCUMENT_PAGE_READY, timeout=max(1, (deadline-time.monotonic())*1000))
    return _projection_text(_evaluate_document(page, _projection_expression(output_mode, request.get('track'), request.get('resource_detail_links', False)), deadline, meta), output_mode, meta, deadline)


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
        if (engine == 'moli' and output_mode in {'document', 'file', 'document_page'}) or (engine == 'lightpanda' and output_mode in {'document', 'file', 'resource', 'media_metadata', 'document_page'}) or (engine == 'obscura' and output_mode == 'document_page'):
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                with native_cdp_browser(playwright, request, start + route_timeout, metadata=meta) as browser:
                    meta['browser_version'] = browser.version
                    page = browser.new_context().new_page()
                    if output_mode in {'resource', 'media_metadata', 'document_page'}:
                        meta['navigation_wait_until'] = 'load'
                        response = page.goto(request['url'], wait_until=meta['navigation_wait_until'], timeout=max(1, (route_timeout-(time.monotonic()-start))*1000))
                        content = _page_projection(page, request, output_mode, meta, start + route_timeout)
                        return dict(ok=response is not None and 200 <= response.status < 300, content=content, html='', status_code=response.status if response else None, final_url=page.url, metadata=meta)
                    native = _native_browser_file(page, request, route_timeout, start, meta)
                    if output_mode == 'file' or not native['ok']:
                        return native
                    raw = base64.b64decode(native.pop('files_b64')[0], validate=True)
                    meta['_raw_stdout_b64'] = base64.b64encode(raw).decode('ascii')
                    extract_start = time.monotonic()
                    text, extractor = extract_document_text(raw)
                    meta.update(document_extractor=extractor,
                        document_extract_ms=round((time.monotonic()-extract_start)*1000, 2))
                    native.update(ok=bool(text.strip()), content=text,
                        error_type=None if text.strip() else 'empty_response')
                    return native
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
                http_proxy=request.get('http_proxy'),
                native_metadata=request.get('native_metadata'),
                track=request.get('track'),
                detail_links=request.get('resource_detail_links', False),
                include_subdocuments=bool(request.get('include_subdocuments')),
            )
            environment = None
            if engine == 'obscura' and request.get('ca_cert'):
                environment = {**os.environ, 'SSL_CERT_FILE': str(request['ca_cert'])}
            from harness.web_fetch.network_dns import native_process_dns_environment
            with native_process_dns_environment(
                request.get('dns_mode', 'system'),
                request.get('native_dns_library'),
                start + route_timeout,
                meta,
                environment,
            ) as process_environment:
                result = subprocess.run(
                    command, capture_output=True, timeout=route_timeout,
                    **({'env': process_environment} if process_environment else {})
                )
            meta.update(browser_exit_code=result.returncode,
                        browser_stderr=result.stderr.decode('utf-8', 'replace'))
            if result.returncode and not request.get('native_metadata'):
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
            if output_mode == 'document':
                raw_fetch_ms = round((time.monotonic()-start)*1000, 2)
                meta['_raw_stdout_b64'] = base64.b64encode(result.stdout).decode('ascii')
                meta['raw_stdout_sha256'] = hashlib.sha256(result.stdout).hexdigest()
                meta['raw_fetch_ms'] = raw_fetch_ms
                text, extractor = extract_document_text(result.stdout)
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
            if output_mode == 'markdown' and request.get('native_metadata'):
                meta['_raw_stdout_b64'] = base64.b64encode(result.stdout).decode('ascii')
                meta['raw_stdout_sha256'] = hashlib.sha256(result.stdout).hexdigest()
                contract = request['native_metadata']
                meta['native_metadata'] = {key: value for key, value in contract.items()
                                           if key not in {'script_path', 'script_source'}}
                envelope = json.loads(browser_output)
                if not isinstance(envelope, dict) or not isinstance(envelope.get('content'), str):
                    raise ValueError('native Markdown envelope requires string content')
                content = envelope['content']
                final_url = envelope.get('url') if engine == 'lightpanda' else envelope.get('final_url')
                final_url = final_url or None
                if final_url is not None:
                    validate_http_url(final_url)
                status = envelope.get('http_status') if engine == 'lightpanda' else None
                status = status if isinstance(status, int) and 100 <= status <= 599 else None
                error = envelope.get('error')
                ok = not result.returncode and not error and bool(content.strip())
                meta['native_markdown_ms'] = round((time.monotonic()-start)*1000, 2)
                return dict(ok=ok, content=content, html='', status_code=status,
                            final_url=final_url, title=envelope.get('title'), metadata=meta,
                            error_type='browser_exit' if result.returncode or error else
                                       None if content.strip() else 'empty_response',
                            error_message=str(error or meta['browser_stderr']) if result.returncode or error else None)
            if output_mode in {'resource', 'media_metadata', 'document_page'}:
                meta['projection_ms'] = round((time.monotonic()-start)*1000, 2)
                content = _projection_text(browser_output, output_mode, meta, start + route_timeout)
                return dict(ok=bool(content.strip()), content=content, html='', status_code=None, final_url=None, metadata=meta)
            if output_mode == 'markdown':
                meta['native_markdown_ms'] = round((time.monotonic()-start)*1000, 2)
                return dict(ok=bool(browser_output.strip()), content=browser_output, html='',
                            status_code=None, final_url=None, metadata=meta,
                            error_type=None if browser_output.strip() else 'empty_response')
            html = browser_output
            status = None
            final_url = None
        else:
            from playwright.sync_api import sync_playwright
            meta['playwright_version'] = version('playwright')
            with sync_playwright() as p:
                fixture_https = bool(request.get('ca_cert')) and request.get('network_policy') == 'loopback_only'
                profile = None
                persistent_context = None
                if engine == 'kitesurf':
                    browser = p.chromium.connect_over_cdp(request['endpoint'], timeout=max(1, (route_timeout-(time.monotonic()-start))*1000), headers={'Authorization': 'Bearer ' + request['api_token']})
                else:
                    launch = dict(executable_path=request['binary'], headless=True,
                                  args=request.get('browser_args') or [], timeout=timeout*1000)
                    if request.get('http_proxy'):
                        launch['proxy'] = {'server': request['http_proxy']}
                    needs_profile = (
                        output_mode in {'file', 'document'}
                        or request.get('dns_mode', 'system') == 'public_doh'
                    )
                    if needs_profile:
                        profile = tempfile.TemporaryDirectory(prefix='browser-profile-')
                        if request.get('dns_mode', 'system') == 'public_doh':
                            from harness.web_fetch.network_dns import (
                                configure_chromium_dns_profile,
                            )
                            meta.update(configure_chromium_dns_profile(
                                profile.name, 'public_doh'
                            ))
                        if output_mode in {'file', 'document'}:
                            # The built-in PDF viewer replaces response bytes
                            # with HTML, so document tasks use native downloads.
                            preferences = Path(profile.name) / 'Default' / 'Preferences'
                            preferences.parent.mkdir()
                            preferences.write_text(json.dumps({
                                'plugins': {'always_open_pdf_externally': True}
                            }))
                        persistent_context = p.chromium.launch_persistent_context(
                            profile.name, accept_downloads=True, ignore_https_errors=fixture_https, **launch)
                        browser = persistent_context.browser
                        if output_mode in {'file', 'document'}:
                            meta['pdf_handling'] = 'native download; viewer disabled in fresh temporary profile'
                    else:
                        browser = p.chromium.launch(**launch)
                try:
                    if browser is not None:
                        if engine == 'kitesurf':
                            meta['cdp_reported_browser_version'] = browser.version
                            meta['engine_version_exposed'] = False
                        else:
                            meta['browser_version'] = browser.version
                    # The benchmark fixture has a per-run CA, not a system-trusted
                    # certificate. Only isolated loopback requests may bypass the
                    # browser's public trust store.
                    context = persistent_context or browser.new_context(ignore_https_errors=fixture_https)
                    page = context.new_page()
                    if output_mode in {'file', 'document'}:
                        native = _native_browser_file(page, request, route_timeout, start, meta)
                        if output_mode == 'file' or not native['ok']:
                            return native
                        raw = base64.b64decode(native.pop('files_b64')[0], validate=True)
                        meta['_raw_stdout_b64'] = base64.b64encode(raw).decode('ascii')
                        if raw.startswith((b'%PDF', b'PK')):
                            extract_start = time.monotonic()
                            text, extractor = extract_document_text(raw)
                            meta.update(document_extractor=extractor,
                                document_extract_ms=round((time.monotonic()-extract_start)*1000, 2))
                            native.update(ok=bool(text.strip()), content=text,
                                error_type=None if text.strip() else 'empty_response')
                            return native
                        raise ValueError('Browser document response is not a supported PDF or DOCX')
                    meta['navigation_wait_until'] = request.get('wait_until') or 'load'
                    response = page.goto(
                        request['url'],
                        wait_until=meta['navigation_wait_until'],
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
                    if output_mode in {'resource', 'media_metadata', 'document_page'}:
                        content = _page_projection(page, request, output_mode, meta, start + route_timeout)
                        return dict(ok=response is not None and 200 <= response.status < 300, content=content, html='', status_code=response.status if response else None, final_url=page.url, metadata=meta)
                    # Avoid Playwright's XMLSerializer helper, which is not
                    # implemented by every CDP engine. Same expression for both.
                    html = _evaluate_document(page, 'document.documentElement.outerHTML', start + route_timeout, meta)
                    final_url = page.url
                    status = response.status if response else None
                finally:
                    if persistent_context is not None:
                        persistent_context.close()
                    elif browser is not None:
                        browser.close()
                    if profile is not None:
                        profile.cleanup()
        meta['fetch_html_ms'] = round((time.monotonic()-start)*1000, 2)
        converter = CONVERTER
        package = require_converter(request.get('node_html_markdown_path'))
        meta['extractor_version'] = json.loads((package / 'package.json').read_text())['version']
        meta['converter_sha256'] = hashlib.sha256(converter.read_bytes()).hexdigest()
        meta['node_version'] = subprocess.run(
            [shutil.which('node') or 'node', '--version'], capture_output=True, text=True,
            check=True, timeout=5).stdout.strip()
        convert_start = time.monotonic()
        converted = subprocess.run(
            converter_command(package), input=html.encode(),
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
                    status_code=meta.get('file_response_status'), final_url=meta.get('file_response_url'),
                    error_type=browser_error_type(error),
                    error_message=str(error)[:4000])


def main():
    print(json.dumps(fetch(json.load(sys.stdin)), ensure_ascii=False))


if __name__ == '__main__':
    main()
