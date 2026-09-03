"""Isolated HTML-to-Markdown browser comparison, with no provider fallback."""
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


def cli_command(engine, binary, url, timeout):
    ms = str(max(1, math.ceil(timeout * 1000)))
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
        if engine != 'kitesurf' and not shutil.which(self.config.get('binary') or engine):
            errors.append('binary')
        for dependency in ('pyhtml2md', 'playwright'):
            if importlib.util.find_spec(dependency) is None:
                errors.append(dependency)
        return errors

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        validate_http_url(case.url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        engine = self.config['engine']
        binary = shutil.which(self.config.get('binary') or engine)
        metadata = {'engine': engine, 'wait_until': 'domcontentloaded',
                    'extractor': 'pyhtml2md', 'session': 'fresh per call',
                    'deployment': 'public cloud CDP' if engine == 'kitesurf' else 'local process'}
        if binary:
            with open(binary, 'rb') as stream:
                metadata['binary_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        request = {'engine': engine, 'binary': binary, 'url': case.url,
                   'timeout': timeout, 'endpoint': self.config.get('endpoint',
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
