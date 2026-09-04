"""Isolated browser-to-Markdown comparison, with no provider fallback."""
from __future__ import annotations

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


def _resolve_binary(engine, configured):
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
    if output_mode not in {'html', 'markdown'}:
        raise ValueError('Unsupported browser output mode: ' + output_mode)
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

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        validate_http_url(case.url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        engine = self.config['engine']
        output_mode = 'markdown' if self.type == 'headless_markdown' else 'html'
        binary = getattr(self, '_resolved_binary', None) or _resolve_binary(
            engine, self.config.get('binary'))
        cli_wait = 'networkidle' if case.track == 'dynamic' else 'done'
        wait_until = ('networkidle2' if engine == 'kitesurf' and output_mode == 'markdown'
                      else cli_wait if output_mode == 'markdown' and engine in {'moli','lightpanda'}
                      else 'load + adaptive settle' if output_mode == 'markdown' and engine == 'obscura'
                      else 'domcontentloaded')
        extractor = ('cloudflare_native_markdown' if engine == 'kitesurf' and output_mode == 'markdown'
                     else f'{engine}_native_markdown' if output_mode == 'markdown' and engine != 'chromium'
                     else 'node-html-markdown')
        metadata = {'engine': engine, 'wait_until': wait_until,
                    'extractor': extractor, 'output_contract': 'markdown',
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
            with open(binary, 'rb') as stream:
                metadata['binary_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
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
        html = result.pop('html', '')
        if html:
            metadata['_rendered_html'] = html
            metadata['rendered_html_sha256'] = hashlib.sha256(html.encode()).hexdigest()
        return ProviderResult(**result, metadata=metadata)
