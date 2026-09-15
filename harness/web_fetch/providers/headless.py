"""Isolated task-native Web Fetch comparison, with no provider fallback."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..models import EvalCase, ProviderResult
from ..retrieval_hints import plan_for_case
from ..safety import validate_http_url
from ..container_resources import ContainerResourceRunner
from .base import Provider

ENGINES = ('kitesurf', 'lightpanda', 'obscura', 'moli', 'chromium')
HEADLESS_CONVERSION = (
    'Native Markdown for Kitesurf, Lightpanda, Obscura and Moli 1.1.1; direct '
    'Moli documents/media use automatic raw output; Chromium '
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


def _cli_completion_arguments(engine, plan):
    if not plan:
        return None
    if engine in {'moli', 'lightpanda'}:
        arguments = [
            '--wait-selector' if plan['wait_kind'] == 'selector' else '--wait-script',
            plan['wait_value'],
            '--wait-until',
            plan['wait_until'],
        ]
        if plan['delay_ms']:
            arguments += [
                '--delay-ms' if engine == 'moli' else '--wait-ms',
                str(plan['delay_ms']),
            ]
        if engine == 'moli' and plan.get('frame_selector'):
            arguments.append('--with-frames')
        return arguments
    if engine == 'obscura':
        # Obscura can wait for one top-level selector but cannot express a
        # selector count or enter a child frame from its one-shot fetch CLI.
        wait_until = 'networkidle0' if plan['wait_until'] == 'networkidle' else plan['wait_until']
        arguments = ['--wait-until', wait_until]
        if not plan.get('frame_selector'):
            arguments += ['--selector', plan['selector']]
        if plan['delay_ms']:
            # Its public CLI accepts whole seconds, and applies this fixed wait
            # before its selector poll rather than after selector completion.
            arguments += ['--wait', str(max(1, math.ceil(plan['delay_ms'] / 1000)))]
        return arguments
    return None


def completion_metadata(engine, plan, output_mode):
    """Describe the exact native semantics used for one normalized plan."""
    if not plan:
        return None
    if output_mode in {'document', 'file'}:
        metadata = {
            'strategy': 'raw_response',
            'completion_support': 'unsupported',
            'minimum_count_enforced': False,
            'unsupported': ['selector', 'minimum_count', 'post_completion_delay'],
        }
    elif engine in {'moli', 'lightpanda', 'chromium'}:
        strategy = (
            'frame_selector_count_script'
            if plan.get('frame_selector')
            else 'selector'
            if plan['wait_kind'] == 'selector'
            else 'selector_count_script'
        )
        metadata = {
            'strategy': strategy,
            'completion_support': 'exact',
            'minimum_count_enforced': True,
            'unsupported': [],
        }
        if engine in {'lightpanda', 'chromium'} and plan.get('frame_selector'):
            # These paths can wait on same-origin child content, but their
            # existing Markdown/outerHTML contract does not serialize it.
            metadata['output_unsupported'] = ['child_frame_serialization']
    elif engine == 'obscura':
        frame = bool(plan.get('frame_selector'))
        # The CLI only warns when its selector poll expires and still exits
        # successfully, so even the one-element condition is observed but not
        # enforced as a request boundary.
        unsupported = [
            'selector_enforcement',
            'post_completion_delay',
            'completion_timeout',
        ]
        if frame:
            unsupported += ['child_frame_selector', 'selector']
        if plan['minimum_count'] != 1:
            unsupported.append('minimum_count')
        metadata = {
            'strategy': 'fixed_delay' if frame else 'fixed_delay_then_selector',
            'completion_support': 'approximate',
            'minimum_count_enforced': False,
            'unsupported': unsupported,
        }
    else:
        raise ValueError('unsupported dynamic engine: ' + engine)
    return {
        **metadata,
        'wait_until': plan['wait_until'],
        'selector': plan['selector'],
        'frame_selector': plan.get('frame_selector'),
        'minimum_count': plan['minimum_count'],
        'timeout_ms': plan['timeout_ms'],
        'delay_ms': plan['delay_ms'],
    }


def cli_command(
    engine,
    binary,
    url,
    timeout,
    output_mode='html',
    wait_until=None,
    retrieval_plan=None,
):
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
        completion = _cli_completion_arguments(engine, retrieval_plan)
        if engine == 'obscura':
            # Obscura's documented default is load plus an adaptive, at-most
            # five-second quiescence wait. Do not replace it with a fixed delay.
            return [binary, 'fetch', '--dump', 'markdown', *(completion or []), '--timeout',
                    str(math.ceil(timeout)), '--quiet', url]
        common = [binary, 'fetch', '--dump', 'markdown']
        common += completion or ['--wait-until', wait_until or 'done']
        if engine == 'moli':
            return common + ['--timeout', ms, '--http-timeout', ms, url]
        if engine == 'lightpanda':
            return common + ['--terminate-ms', ms, '--http-timeout', ms, url]
        raise ValueError('Not a native Markdown CLI engine: ' + engine)
    completion = _cli_completion_arguments(engine, retrieval_plan)
    common = [binary, 'fetch', '--dump', 'html']
    common += completion or ['--wait-until', 'domcontentloaded']
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
        resource_interval = self.config.get('resource_sample_interval_ms')
        if resource_interval is not None:
            if engine == 'kitesurf' and self.type == 'headless_markdown':
                errors.extend(name for name in ('api_token', 'account_id')
                              if not self.config.get(name))
            if not self.config.get('resource_container_image'):
                errors.append('resource_container_image')
            if engine != 'kitesurf' and not self.config.get('resource_binary'):
                errors.append('resource_binary')
            if not errors:
                try:
                    self._resource_runner().validate()
                except Exception as exc:
                    errors.append('docker_cgroup_v2:' + str(exc))
            self._resolved_binary = self.config.get('resource_binary')
            return errors
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

    def _resource_runner(self):
        return ContainerResourceRunner(
            self.config['resource_container_image'],
            interval_ms=int(self.config['resource_sample_interval_ms']),
            cpus=float(self.config.get('resource_cpus', 2.0)),
            memory=self.config.get('resource_memory', '2g'),
            pids_limit=int(self.config.get('resource_pids_limit', 512)),
        )

    def implementation_identity(self, *, refresh=False):
        """Snapshot the implementation without exposing paths or credentials."""
        if not refresh and hasattr(self, '_implementation_identity'):
            return dict(self._implementation_identity)
        engine = self.config['engine']
        if self.config.get('resource_sample_interval_ms') is not None:
            resource = self._resource_runner().implementation_identity(
                self.config.get('resource_binary'),
                version_args=['version'] if engine == 'lightpanda' else None,
            )
            identity = {
                'identity_source': (
                    'immutable container image and executable bytes observed before calls'
                ),
                'engine': engine,
                'deployment': (
                    'Cloudflare Browser Run account API from isolated client container'
                    if engine == 'kitesurf' else 'isolated Linux client container'
                ),
                **resource,
            }
            self._implementation_identity = identity
            return dict(identity)
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
        try:
            retrieval_plan = plan_for_case(case)
        except ValueError as error:
            return ProviderResult(False, '', error_type='configuration',
                                  error_message=str(error))
        engine = self.config['engine']
        resource_interval = self.config.get('resource_sample_interval_ms')
        output_mode = output_mode_for_case(engine, self.type, case)
        binary = (self.config.get('resource_binary') if resource_interval is not None else
                  getattr(self, '_resolved_binary', None) or _resolve_binary(
                      engine, self.config.get('binary')))
        cli_wait = retrieval_plan.wait_until if retrieval_plan else 'done'
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
                    'deployment': ('Cloudflare Browser Run from isolated client container'
                                   if resource_interval is not None and engine == 'kitesurf'
                                   else 'isolated Linux client container'
                                   if resource_interval is not None
                                   else 'public cloud CDP' if engine == 'kitesurf'
                                   else 'local process')}
        normalized_plan = retrieval_plan.worker_dict() if retrieval_plan else None
        if normalized_plan and engine != 'kitesurf':
            metadata['completion'] = completion_metadata(engine, normalized_plan, output_mode)
            metadata['completion']['effective_route_timeout_ms'] = round(
                min(timeout, retrieval_plan.timeout_ms / 1000) * 1000
            )
        if engine == 'kitesurf' and output_mode == 'markdown' and resource_interval is None:
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
                   'task_kind': case.task_kind,
                   'browser_args': ['--no-sandbox']
                   if resource_interval is not None and engine == 'chromium' else [],
                   'wait_until': retrieval_plan.wait_until if retrieval_plan else None,
                   'track': case.track,
                   'retrieval_plan': normalized_plan,
                   'endpoint': self.config.get('endpoint',
                   'wss://kitesurf.cloudflare.app/devtools/browser')}
        if engine == 'kitesurf':
            request.update({
                'remote_markdown_api': True,
                'account_id': self.config['account_id'],
                'api_token': self.config['api_token'],
                'browser': 'kitesurf',
                'wait_until': self.config.get('wait_until', 'networkidle2'),
                'max_bytes': self.config.get('max_bytes', 20_000_000),
            })
        worker = Path(__file__).resolve().parents[3] / 'scripts/headless_worker.py'
        metadata['worker_sha256'] = hashlib.sha256(worker.read_bytes()).hexdigest()
        if request['browser_args']:
            metadata['browser_launch_args'] = request['browser_args']
        limit = 40_000_000
        if resource_interval is not None:
            call = self._resource_runner().run(request, timeout=timeout)
            raw = call.stdout
            diagnostic = call.stderr.decode('utf-8', 'replace')[:limit]
            metadata.update(
                exit_code=call.exit_code,
                stderr=diagnostic,
                stdout_bytes=len(call.stdout),
                stdout_truncated=len(call.stdout) > limit,
                stderr_bytes=len(call.stderr),
                stderr_truncated=len(call.stderr) > limit,
                client_resource=call.resource,
                resource_environment=call.environment,
            )
            stopped = 'timeout' if call.exit_code == 124 else None
        else:
            raw, diagnostic, stopped, returncode, stdout_bytes, stderr_bytes = self._run_local_worker(
                worker, request, timeout, limit
            )
            metadata.update(
                exit_code=returncode,
                stderr=diagnostic,
                stdout_bytes=stdout_bytes,
                stdout_truncated=stdout_bytes > limit,
                stderr_bytes=stderr_bytes,
                stderr_truncated=stderr_bytes > limit,
            )
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

    @staticmethod
    def _run_local_worker(worker, request, timeout, limit):
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            command = [sys.executable, str(worker)]
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
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
            stdout_bytes = os.fstat(stdout.fileno()).st_size
            stderr_bytes = os.fstat(stderr.fileno()).st_size
            stdout.seek(0); raw = stdout.read(limit+1)
            stderr.seek(0); diagnostic_raw = stderr.read(limit+1)
            diagnostic = diagnostic_raw[:limit].decode('utf-8', 'replace')
        return raw, diagnostic, stopped, process.returncode, stdout_bytes, stderr_bytes
