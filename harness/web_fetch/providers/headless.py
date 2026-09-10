"""Isolated task-native Web Fetch comparison, with no provider fallback."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .base import Provider
from ..models import EvalCase, ProviderResult
from ..safety import validate_http_url

ENGINES = ('kitesurf', 'lightpanda', 'obscura', 'moli', 'chromium')
HEADLESS_CONVERSION = (
    'Native Markdown for Kitesurf, Lightpanda, Obscura and three measured Moli '
    'versions; direct Moli documents/media use automatic raw output; Chromium '
    'whole-DOM HTML via node-html-markdown 2.0.0'
)


def _resolve_binary(engine, configured):
    # An explicitly expanded empty environment variable is a configuration
    # error.  It must not behave like an omitted option and silently select a
    # different Playwright browser during a partial rerun.
    if configured == '':
        return None
    resolved = shutil.which(configured or engine)
    if resolved or engine != 'chromium' or configured not in (None, '', 'chromium'):
        return resolved
    # Playwright owns the Chromium revision used by this adapter. Requiring a
    # second PATH alias can silently select a different system browser.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        bundled = playwright.chromium.executable_path
    return bundled if Path(bundled).is_file() else None


def cli_command(engine, binary, url, timeout, output_mode='html', wait_until=None):
    ms = str(max(1, math.ceil(timeout * 1000)))
    if output_mode not in {'html', 'markdown', 'document', 'file'}:
        raise ValueError('Unsupported browser output mode: ' + output_mode)
    if output_mode in {'document', 'file'}:
        if engine != 'moli':
            raise ValueError('Raw document auto-output is only supported by Moli')
        # All three measured Moli releases write direct raw responses such as
        # PDF and media bytes when --dump is omitted. Supplying --dump markdown
        # rejects those responses before extraction can begin. Version 1.1.3
        # documents this behavior explicitly; 1.0.6 and 1.1.1 are bound to
        # release binaries and covered by route probes and end-to-end tests.
        return [binary, 'fetch', '--timeout', ms, '--http-timeout', ms, url]
    if output_mode == 'markdown':
        if engine == 'obscura':
            # Obscura's documented default is load plus an adaptive, at-most
            # five-second quiescence wait. Do not replace it with a fixed delay.
            return [binary, 'fetch', '--dump', 'markdown', '--timeout',
                    str(math.ceil(timeout)), '--quiet', url]
        common = [binary, 'fetch', '--dump', 'markdown', '--wait-until', wait_until or 'done']
        if engine == 'moli':
            return common + ['--timeout', ms, '--http-timeout', ms, url]
        if engine == 'lightpanda':
            return common + ['--terminate-ms', ms, '--http-timeout', ms, url]
        raise ValueError('Not a native Markdown CLI engine: ' + engine)
    common = [binary, 'fetch', '--dump', 'html', '--wait-until', 'domcontentloaded']
    if engine == 'obscura':
        return common + ['--timeout', str(math.ceil(timeout)), '--quiet', url]
    if engine == 'moli':
        return common + ['--timeout', ms, '--http-timeout', ms, url]
    if engine == 'lightpanda':
        return common + ['--http-timeout', ms, url]
    raise ValueError('Not a CLI engine: ' + engine)


def output_mode_for_case(engine, provider_type, case):
    """Choose the fastest version-supported route for the requested deliverable."""
    document_url = case.url.casefold().split('?', 1)[0]
    direct_document = (engine == 'moli' and case.track == 'document'
                       and (document_url.endswith(('.pdf', '.docx'))
                            or '/pdf/' in document_url))
    if engine == 'moli' and case.task_kind == 'file':
        return 'file'
    if direct_document:
        return 'document'
    return 'markdown' if provider_type == 'headless_markdown' else 'html'


class HeadlessProvider(Provider):
    def validate(self):
        errors = []
        engine = self.config.get('engine')
        if engine not in ENGINES:
            errors.append('engine')
        binary = _resolve_binary(engine, self.config.get('binary'))
        if engine != 'kitesurf' and not binary:
            errors.append('binary')
        self._resolved_binary = binary
        if engine == 'kitesurf' and self.type == 'headless_markdown':
            errors.extend(name for name in ('api_token', 'account_id') if not self.config.get(name))
        dependencies = ('playwright',) if engine in {'chromium'} or self.type == 'headless_html' else ()
        for dependency in dependencies:
            if importlib.util.find_spec(dependency) is None:
                errors.append(dependency)
        if engine == 'chromium' or self.type == 'headless_html':
            if not shutil.which('node'):
                errors.append('node')
            package = Path(__file__).resolve().parents[3] / 'node_modules/node-html-markdown/package.json'
            if not package.is_file():
                errors.append('node-html-markdown')
        return errors

    def implementation_identity(self, *, refresh=False):
        """Snapshot the implementation without exposing paths or credentials."""
        if not refresh and hasattr(self, '_implementation_identity'):
            return dict(self._implementation_identity)
        engine = self.config['engine']
        if engine == 'kitesurf':
            identity = {
                'identity_source': 'provider product and endpoint observed before calls; hosted backend version is not exposed',
                'engine': engine,
                'deployment': 'Cloudflare Browser Run account API',
                'endpoint': 'Browser Rendering /markdown with browser=kitesurf',
                'binary_sha256': None,
                'reported_version': None,
            }
            self._implementation_identity = identity
            return dict(identity)
        binary = getattr(self, '_resolved_binary', None) or _resolve_binary(
            engine, self.config.get('binary'))
        if not binary:
            raise ValueError(self.name + ': executable is unavailable')
        with open(binary, 'rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        reported = None
        probe_error = None
        try:
            version = subprocess.run([binary, '--version'], capture_output=True, text=True,
                                     timeout=5, check=False)
            candidate = (version.stdout or version.stderr).strip()[:200]
            if version.returncode == 0 and candidate:
                reported = candidate
            else:
                probe_error = f'exit {version.returncode}'
        except (OSError, subprocess.TimeoutExpired) as exc:
            probe_error = type(exc).__name__
        identity = {
            'identity_source': 'executable bytes observed outside the timed calls',
            'engine': engine,
            'deployment': 'local process',
            'binary_sha256': digest,
            'reported_version': reported,
            'version_probe_error': probe_error,
        }
        self._implementation_identity = identity
        return dict(identity)

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        validate_http_url(case.url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        engine = self.config['engine']
        output_mode = output_mode_for_case(engine, self.type, case)
        binary = getattr(self, '_resolved_binary', None) or _resolve_binary(
            engine, self.config.get('binary'))
        cli_wait = 'networkidle' if case.track == 'dynamic' else 'done'
        wait_until = ('automatic raw response' if output_mode == 'file'
                      else 'automatic raw response or serialized HTML' if output_mode == 'document'
                      else 'networkidle2' if engine == 'kitesurf' and output_mode == 'markdown'
                      else cli_wait if output_mode == 'markdown' and engine in {'moli','lightpanda'}
                      else 'load + adaptive settle' if output_mode == 'markdown' and engine == 'obscura'
                      else 'domcontentloaded')
        extractor = ('moli_auto_raw' if output_mode == 'file'
                     else 'moli_auto_raw+document_text' if output_mode == 'document'
                     else 'cloudflare_native_markdown' if engine == 'kitesurf' and output_mode == 'markdown'
                     else f'{engine}_native_markdown' if output_mode == 'markdown' and engine != 'chromium'
                     else 'node-html-markdown')
        output_contract = ('original_file_bytes' if output_mode == 'file' else
                           'readable_document_text' if output_mode == 'document' else
                           'markdown')
        metadata = {'engine': engine, 'wait_until': wait_until,
                    'extractor': extractor, 'output_contract': output_contract,
                    'session': 'fresh per call',
                    'deployment': 'public cloud CDP' if engine == 'kitesurf' else 'local process'}
        if engine == 'kitesurf' and output_mode == 'markdown':
            from .cloudflare import CloudflareMarkdownProvider
            cloudflare = CloudflareMarkdownProvider(self.name, {
                'account_id': self.config['account_id'], 'api_token': self.config['api_token'],
                'browser': 'kitesurf', 'wait_until': self.config.get('wait_until', 'networkidle2'),
                'max_bytes': self.config.get('max_bytes', 20_000_000),
            })
            result = cloudflare.fetch(case, timeout)
            result.metadata = {**metadata, **result.metadata,
                'deployment': 'Cloudflare Browser Run account API'}
            return result
        if binary:
            identity = self.implementation_identity()
            metadata['binary_sha256'] = identity['binary_sha256']
            if identity['reported_version']:
                metadata['browser_version'] = identity['reported_version']
            if identity['version_probe_error']:
                metadata['browser_version_probe_error'] = identity['version_probe_error']
        request = {'engine': engine, 'binary': binary, 'url': case.url,
                   'timeout': timeout, 'output_mode': output_mode,
                   'wait_until': 'networkidle' if case.track == 'dynamic' else None,
                   'endpoint': self.config.get('endpoint',
                   'wss://kitesurf.cloudflare.app/devtools/browser')}
        worker = Path(__file__).resolve().parents[3] / 'scripts/headless_worker.py'
        metadata['worker_sha256'] = hashlib.sha256(worker.read_bytes()).hexdigest()
        limit = 40_000_000
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen([sys.executable, str(worker)], stdin=subprocess.PIPE,
                stdout=stdout, stderr=stderr, start_new_session=True)
            process.stdin.write(json.dumps(request).encode())
            process.stdin.close()
            deadline = time.monotonic() + timeout
            stopped = None
            while process.poll() is None:
                if stdout.tell() > limit or stderr.tell() > limit:
                    stopped = 'output_too_large'
                    break
                if time.monotonic() >= deadline:
                    stopped = 'timeout'
                    break
                try:
                    process.wait(timeout=min(.05, max(.001, deadline-time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            if stopped:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Some managed runners forbid process-group signals while
                    # permitting signals to a child created by this process.
                    process.kill()
                process.wait()
            stdout.seek(0); raw = stdout.read(limit+1)
            stderr.seek(0); diagnostic = stderr.read(4096).decode('utf-8', 'replace')
        metadata.update(exit_code=process.returncode, stderr=diagnostic)
        if stopped or len(raw) > limit:
            return ProviderResult(False, '', error_type=stopped or 'output_too_large',
                                  error_message='Isolated fetch exceeded budget', metadata=metadata)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return ProviderResult(False, '', error_type='worker_error',
                                  error_message=diagnostic or 'Invalid worker response', metadata=metadata)
        metadata.update(result.pop('metadata', {}))
        files_b64 = result.pop('files_b64', [])
        if files_b64:
            result['files'] = [{'data': base64.b64decode(value, validate=True)}
                               for value in files_b64]
        html = result.pop('html', '')
        if html:
            metadata['_rendered_html'] = html
            metadata['rendered_html_sha256'] = hashlib.sha256(html.encode()).hexdigest()
        return ProviderResult(**result, metadata=metadata)
