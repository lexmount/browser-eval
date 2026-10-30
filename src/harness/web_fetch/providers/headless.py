"""Isolated task-native Web Fetch comparison, with no provider fallback."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.paths import validate_http_url

from harness.web_fetch.markdown import converter_package, require_converter
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.network_dns import validate_dns_mode, validate_native_dns_library
from harness.web_fetch.providers.base import (
    Provider, await_owned_process_exit, terminate_owned_process_tree,
)
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.providers.runtime_identity import require_expected_provider_versions
from harness.web_fetch.retrieval_hints import native_contract_for_case, plan_for_case

ENGINES = ('kitesurf', 'lightpanda', 'obscura', 'moli', 'chromium')
LOOPBACK_SANDBOX_PROFILE = (
    '(version 1)(allow default)(deny network*)'
    '(allow network* (local unix-socket))'
    '(allow network* (remote unix-socket))'
    '(allow network-outbound (remote ip "localhost:*"))'
)
HEADLESS_CONVERSION = (
    'Native Markdown for page, embedded-document and webpage-media tasks when the '
    'browser supplies it; task-native browser bytes for files; Chromium DOM HTML '
    'uses node-html-markdown 2.0.0 for every page deliverable'
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
    elif engine in {'moli', 'chromium'} or (
        engine == 'lightpanda' and plan['wait_kind'] == 'selector'
    ):
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
        if engine == 'chromium' and plan.get('frame_selector'):
            # These paths can wait on same-origin child content, but their
            # existing Markdown/outerHTML contract does not serialize it.
            metadata['output_unsupported'] = ['child_frame_serialization']
    elif engine == 'lightpanda':
        # Lightpanda 1.0.0 accepts --wait-script but does not enforce the
        # predicate: even `() => false` returns a successful response
        # immediately.  Keep using the official flag so the attempted route is
        # reproducible, but do not describe count, frame or text-completeness
        # predicates as an exact request boundary.
        metadata = {
            'strategy': 'native_script_not_enforced',
            'completion_support': 'unsupported',
            'minimum_count_enforced': False,
            'unsupported': [
                'script_enforcement', 'minimum_count', 'minimum_text_fragments',
            ],
        }
        if plan.get('frame_selector'):
            metadata['unsupported'].append('child_frame_selector')
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
        'minimum_text_fragments': plan.get('minimum_text_fragments', 0),
        'timeout_ms': plan['timeout_ms'],
        'delay_ms': plan['delay_ms'],
    }


def _native_script_bytes(metadata):
    if 'script_source' in metadata:
        if 'script_path' in metadata or not isinstance(metadata['script_source'], str):
            raise ValueError('native Markdown script source is ambiguous or invalid')
        source = metadata['script_source'].encode('utf-8')
    else:
        source = Path(metadata['script_path']).read_bytes()
    if hashlib.sha256(source).hexdigest() != metadata['script_sha256']:
        raise ValueError('native Markdown script hash mismatch')
    return source


def _worker_native_metadata(metadata):
    if not metadata or metadata.get('format') != 'obscura_eval':
        return metadata
    source = _native_script_bytes(metadata)
    return {**{key: value for key, value in metadata.items() if key != 'script_path'},
            'script_source': source.decode('utf-8')}


def cli_command(
    engine,
    binary,
    url,
    timeout,
    output_mode='html',
    wait_until=None,
    retrieval_plan=None,
    allow_private_network=False,
    ca_cert=None,
    http_proxy=None,
    native_metadata=None,
    track=None,
    detail_links=False,
    include_subdocuments=False,
):
    if native_metadata:
        if output_mode != 'markdown':
            raise ValueError('native metadata requires native Markdown completion')
        if engine == 'lightpanda' and native_metadata.get('format') == 'lightpanda_json':
            return cli_command(
                engine, binary, url, timeout, output_mode,
                wait_until=wait_until,
                retrieval_plan=retrieval_plan,
                allow_private_network=allow_private_network,
                ca_cert=ca_cert,
                http_proxy=http_proxy,
                track=track,
                detail_links=detail_links,
                include_subdocuments=include_subdocuments,
            ) + ['--json']
        if retrieval_plan:
            raise ValueError('native metadata requires native Markdown completion')
        if engine == 'obscura' and native_metadata.get('format') == 'obscura_eval':
            source = _native_script_bytes(native_metadata)
            expression = ('JSON.stringify({content:(' + source.decode('utf-8')
                          + '),final_url:location.href,title:document.title})')
            prefix = [binary, '--allow-private-network'] if allow_private_network else [binary]
            if http_proxy:
                prefix += ['--proxy', http_proxy]
            return prefix + ['fetch', '--eval', expression, '--timeout',
                             str(math.ceil(timeout)), '--quiet', url]
        raise ValueError('unsupported native metadata route')
    ms = str(max(1, math.ceil(timeout * 1000)))
    if output_mode in {'resource', 'media_metadata', 'document_page'}:
        from harness.web_fetch.task_outputs import (
            DOCUMENT_PAGE_EXPRESSION,
            MEDIA_METADATA_EXPRESSION,
            resource_expression,
        )
        expression = resource_expression(track, detail_links) if output_mode == 'resource' else {'media_metadata': MEDIA_METADATA_EXPRESSION, 'document_page': DOCUMENT_PAGE_EXPRESSION}[output_mode]
        if engine == 'obscura':
            command = [binary, 'fetch', '--eval', expression, '--timeout', str(math.ceil(timeout)), '--quiet']
            if allow_private_network:
                command.append('--allow-private-network')
            if http_proxy:
                command += ['--proxy', http_proxy]
            return command + [url]
        raise ValueError('Rendered projection requires a native CDP or eval interface')
    if output_mode not in {'html', 'markdown', 'document', 'file'}:
        raise ValueError('Unsupported browser output mode: ' + output_mode)
    if output_mode in {'document', 'file'}:
        if engine == 'obscura':
            command = [binary, 'fetch', '--dump', 'original']
            if allow_private_network:
                command.append('--allow-private-network')
            if http_proxy:
                command += ['--proxy', http_proxy]
            return command + ['--timeout', str(math.ceil(timeout)), '--quiet', url]
        if engine != 'moli':
            raise ValueError('Raw document auto-output is only supported by Moli')
        # All three measured Moli releases write direct raw responses such as
        # PDF and media bytes when --dump is omitted. Supplying --dump markdown
        # rejects those responses before extraction can begin. Version 1.1.3
        # documents this behavior explicitly; 1.0.6 and 1.1.1 are bound to
        # release binaries and covered by route probes and end-to-end tests.
        command = [binary, 'fetch']
        if http_proxy:
            command += ['--http-proxy', http_proxy]
        return command + ['--timeout', ms, '--http-timeout', ms, url]
    if output_mode == 'markdown':
        completion = _cli_completion_arguments(engine, retrieval_plan)
        if engine == 'obscura':
            # Obscura's documented default is load plus an adaptive, at-most
            # five-second quiescence wait. Do not replace it with a fixed delay.
            prefix = [binary, '--allow-private-network'] if allow_private_network else [binary]
            if http_proxy:
                prefix += ['--proxy', http_proxy]
            return prefix + ['fetch', '--dump', 'markdown', *(completion or []), '--timeout',
                    str(math.ceil(timeout)), '--quiet', url]
        common = [binary, 'fetch', '--dump', 'markdown']
        if include_subdocuments and engine in {'moli', 'lightpanda'}:
            common.append('--with-frames')
            if engine == 'lightpanda':
                common += ['--load-resources', 'iframe']
        if http_proxy:
            common += ['--http-proxy', http_proxy]
        common += completion or ['--wait-until', wait_until or 'done']
        if engine == 'moli':
            return common + (['--ca-cert', str(ca_cert)] if ca_cert else []) + ['--timeout', ms, '--http-timeout', ms, url]
        if engine == 'lightpanda':
            return common + (['--ca-cert', str(ca_cert)] if ca_cert else []) + ['--terminate-ms', ms, '--http-timeout', ms, url]
        raise ValueError('Not a native Markdown CLI engine: ' + engine)
    completion = _cli_completion_arguments(engine, retrieval_plan)
    common = [binary, 'fetch', '--dump', 'html']
    if http_proxy:
        common += ['--http-proxy' if engine in {'moli', 'lightpanda'} else '--proxy', http_proxy]
    common += completion or ['--wait-until', 'domcontentloaded']
    if engine == 'obscura':
        return common + ['--timeout', str(math.ceil(timeout)), '--quiet', url]
    if engine == 'moli':
        return common + ['--timeout', ms, '--http-timeout', ms, url]
    if engine == 'lightpanda':
        return common + ['--http-timeout', ms, url]
    raise ValueError('Not a CLI engine: ' + engine)


def output_mode_for_case(engine, provider_type, case):
    """Choose one product-native route for each task family."""
    if engine in ENGINES and case.task_kind == 'file':
        return 'file'
    if engine == 'moli' and case.track == 'document':
        return 'markdown'
    native_markdown = (
        provider_type == 'headless_markdown'
        and engine in {'lightpanda', 'obscura', 'kitesurf'}
    )
    if native_markdown and (
        case.track == 'document'
        or (case.task_kind == 'resource' and case.track in {'image', 'video'})
        or (case.task_kind == 'body' and case.track == 'video')
    ):
        return 'markdown'
    if engine == 'chromium' and (
        case.track == 'document'
        or (case.task_kind == 'resource' and case.track in {'image', 'video'})
        or (case.task_kind == 'body' and case.track == 'video')
    ):
        return 'markdown'
    # Document text must come from the selected browser content interface.
    # Downloaded bytes must not be turned into readable text by the harness.
    if case.track == 'document' and (urlsplit(case.url).path.casefold().endswith('.html') or urlsplit(case.url).path.endswith('/')):
        return 'document_page'
    if case.track == 'document':
        return 'markdown' if provider_type == 'headless_markdown' else 'html'
    if case.task_kind == 'resource' and case.track in {'image', 'video'}:
        return 'resource'
    if case.task_kind == 'body' and case.track == 'video':
        return 'markdown' if engine == 'moli' else 'media_metadata'
    return 'markdown' if provider_type == 'headless_markdown' else 'html'


class HeadlessProvider(Provider):
    def __init__(self, name, config):
        super().__init__(name, config)
        self._process_lock = threading.Lock()
        self._active_process = None
        self._cancel_requested = False

    def begin_call(self):
        with self._process_lock:
            self._active_process = None
            self._cancel_requested = False

    def cancel(self):
        with self._process_lock:
            self._cancel_requested = True
            terminate_owned_process_tree(self._active_process)

    @property
    def cancellation_cleanup_seconds(self):
        # Remote and resource-container calls retain their own boundaries.
        local = (self.config.get('engine') != 'kitesurf'
                 and self.config.get('resource_sample_interval_ms') is None)
        return 1.0 if local else 0.0

    def validate(self):
        errors = []
        engine = self.config.get('engine')
        if engine not in ENGINES:
            errors.append('engine')
        native_metadata = self.config.get('native_metadata')
        if native_metadata is not None:
            try:
                if not isinstance(native_metadata, dict):
                    raise ValueError('native metadata must be an object')
                cli_command(engine, self.config.get('binary', 'browser'),
                            'https://example.com/', 1, 'markdown',
                            native_metadata=native_metadata)
                if self.type != 'headless_markdown':
                    raise ValueError('native metadata requires headless_markdown')
            except (KeyError, OSError, ValueError) as error:
                errors.append('native_metadata:' + str(error))
        resource_interval = self.config.get('resource_sample_interval_ms')
        try:
            dns_mode = validate_dns_mode(self.config.get('dns_mode', 'system'))
            if engine == 'kitesurf' and dns_mode != 'system':
                errors.append('hosted Kitesurf owns its DNS environment')
            elif dns_mode == 'public_doh':
                if resource_interval is not None:
                    errors.append('public_doh is unavailable inside resource containers')
                elif engine != 'chromium':
                    validate_native_dns_library(self.config.get('native_dns_library'))
        except ValueError as error:
            errors.append(str(error))
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
            return self._expected_version_errors(errors)
        binary = _resolve_binary(engine, self.config.get('binary'))
        if engine != 'kitesurf' and not binary:
            errors.append('binary')
        self._resolved_binary = binary
        if engine == 'kitesurf' and self.type == 'headless_markdown':
            errors.extend(name for name in ('api_token', 'account_id') if not self.config.get(name))
        if self.config.get('network_policy') == 'loopback_only':
            if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
                errors.append('macos sandbox-exec for loopback_only network policy')
        dependencies = ('playwright',) if engine in {'chromium'} or self.type == 'headless_html' else ()
        for dependency in dependencies:
            if importlib.util.find_spec(dependency) is None:
                errors.append(dependency)
        if engine == 'chromium' or self.type == 'headless_html':
            if not shutil.which('node'):
                errors.append('node')
            try:
                require_converter(self.config.get('node_html_markdown_path'))
            except ValueError as error:
                errors.append(str(error))
        return self._expected_version_errors(errors)

    def _expected_version_errors(self, errors):
        if not errors and self.config.get('expected_version') is not None:
            try:
                require_expected_provider_versions(
                    {self.name: self.implementation_identity()}, {self.name: self.config})
            except ValueError as error:
                errors.append(str(error))
        return errors

    def _resource_runner(self):
        from harness.web_fetch.container_resources import ContainerResourceRunner

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
            version_args = ['version'] if engine == 'lightpanda' else ['--version']
            version = subprocess.run([binary, *version_args], capture_output=True, text=True,
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
            native_contract = native_contract_for_case(case)
        except ValueError as error:
            return ProviderResult(False, '', error_type='configuration',
                                  error_message=str(error))
        engine = self.config['engine']
        resource_interval = self.config.get('resource_sample_interval_ms')
        output_mode = output_mode_for_case(engine, self.type, case)
        binary = (self.config.get('resource_binary') if resource_interval is not None else
                  getattr(self, '_resolved_binary', None) or _resolve_binary(
                      engine, self.config.get('binary')))
        completion_plan = retrieval_plan or (
            native_contract.completion_plan(max(1, math.ceil(timeout * 1000)))
            if native_contract else None
        )
        cli_wait = completion_plan.wait_until if completion_plan else 'done'
        wait_until = ('browser response bytes or completed native download' if output_mode in {'file','document'} and engine == 'chromium'
                      else 'navigation response bytes' if output_mode in {'file','document'} and engine in {'lightpanda','kitesurf'}
                      else 'automatic raw response' if output_mode == 'file'
                      else 'automatic raw response or serialized HTML' if output_mode == 'document'
                      else 'networkidle2' if engine == 'kitesurf' and output_mode == 'markdown'
                      else cli_wait if output_mode == 'markdown' and engine in {'moli','lightpanda'}
                      else 'load + adaptive settle' if output_mode == 'markdown' and engine == 'obscura'
                      else 'load + adaptive settle' if engine == 'obscura' and output_mode in {'resource','media_metadata'}
                      else self.config.get('cdp_wait_until', 'load') if engine == 'kitesurf'
                      else 'load')
        extractor = ('browser_native_response_or_download' if output_mode == 'file' and engine == 'chromium'
                     else 'kitesurf_cdp_response' if output_mode == 'file' and engine == 'kitesurf'
                     else 'lightpanda_cdp_response' if output_mode == 'file' and engine == 'lightpanda'
                     else 'obscura_original_response' if output_mode == 'file' and engine == 'obscura'
                     else 'moli_cdp_response' if output_mode == 'file'
                     else 'browser_native_response_or_download+document_text' if output_mode == 'document' and engine == 'chromium'
                     else 'kitesurf_cdp_response+document_text' if output_mode == 'document' and engine == 'kitesurf'
                     else 'lightpanda_cdp_response+document_text' if output_mode == 'document' and engine == 'lightpanda'
                     else 'obscura_original_response+document_text' if output_mode == 'document' and engine == 'obscura'
                     else 'moli_cdp_response+document_text' if output_mode == 'document'
                     else 'cloudflare_native_markdown' if engine == 'kitesurf' and output_mode == 'markdown'
                     else f'{engine}_native_markdown' if output_mode == 'markdown' and engine != 'chromium'
                     else 'rendered_dom_resources' if output_mode == 'resource'
                     else 'rendered_body_and_standard_media_metadata' if output_mode == 'media_metadata'
                     else 'browser_runtime_document_text' if output_mode == 'document_page'
                     else 'node-html-markdown')
        output_contract = ('original_file_bytes' if output_mode == 'file' else
                           'readable_document_text' if output_mode in {'document','document_page'} else
                           'structured_resource_urls' if output_mode == 'resource' else
                           'readable_media_metadata' if output_mode == 'media_metadata' else 'markdown')
        metadata = {'engine': engine, 'wait_until': wait_until,
                    'extractor': extractor, 'output_contract': output_contract,
                    'dns_mode': self.config.get('dns_mode', 'system'),
                    'session': 'fresh per call',
                    'network_policy': self.config.get('network_policy', 'unrestricted'),
                    'deployment': ('Cloudflare Browser Run from isolated client container'
                                   if resource_interval is not None and engine == 'kitesurf'
                                   else 'isolated Linux client container'
                                   if resource_interval is not None
                                   else 'public cloud CDP' if engine == 'kitesurf'
                                   else 'local process')}
        if output_mode in {'resource', 'media_metadata', 'document_page'}:
            from harness.web_fetch.task_outputs import (
                DOCUMENT_PAGE_EXPRESSION,
                MEDIA_METADATA_EXPRESSION,
                resource_detail_links,
                resource_expression,
            )
            projection = resource_expression(case.track, resource_detail_links(case)) if output_mode == 'resource' else {'media_metadata': MEDIA_METADATA_EXPRESSION, 'document_page': DOCUMENT_PAGE_EXPRESSION}[output_mode]
            metadata['output_projection_sha256'] = hashlib.sha256(projection.encode('utf-8')).hexdigest()
        normalized_plan = completion_plan.worker_dict() if completion_plan else None
        if normalized_plan is not None and native_contract is not None:
            normalized_plan['include_subdocuments'] = native_contract.include_subdocuments
        subdocument_tracks = self.config.get('include_subdocuments_by_track', {})
        if (not isinstance(subdocument_tracks, dict)
                or any(not isinstance(value, bool) for value in subdocument_tracks.values())):
            raise ValueError('include_subdocuments_by_track must map tracks to booleans')
        include_subdocuments = bool(
            (native_contract and native_contract.include_subdocuments)
            or subdocument_tracks.get(case.track, False)
        )
        if normalized_plan and engine != 'kitesurf':
            metadata['completion'] = completion_metadata(engine, normalized_plan, output_mode)
            metadata['completion']['effective_route_timeout_ms'] = round(
                min(timeout, retrieval_plan.timeout_ms / 1000) * 1000
                if retrieval_plan else timeout * 1000
            )
        if include_subdocuments:
            interface = (
                '--load-resources iframe + --with-frames'
                if engine == 'lightpanda' and output_mode == 'markdown'
                else '--with-frames'
                if engine == 'moli' and output_mode == 'markdown'
                else None
            )
            metadata['subdocuments'] = {
                'requested': True,
                'support': (
                    'native' if engine == 'moli' and interface
                    else 'native_markdown_omits_child_document'
                    if engine == 'lightpanda' and interface
                    else 'unsupported'
                ),
                'interface': interface,
                'harness_content_assembly': False,
            }
        if engine == 'kitesurf' and output_mode == 'markdown' and resource_interval is None:
            cloudflare = CloudflareMarkdownProvider(self.name, {
                'account_id': self.config['account_id'], 'api_token': self.config['api_token'],
                'browser': 'kitesurf', 'wait_until': self.config.get('wait_until', 'networkidle2'),
                'max_bytes': self.config.get('max_bytes', 20_000_000),
                'page_output': 'native_markdown',
                'uniform_native_parameters': self.config.get('uniform_native_parameters', False),
                'endpoint': 'wss://api.cloudflare.com/client/v4/accounts/' + self.config['account_id'] + '/browser-run/devtools/browser?browser=kitesurf',
            })
            result = cloudflare.fetch(case, timeout)
            result.metadata = {**metadata, **result.metadata,
                'deployment': 'Cloudflare Browser Run account API'}
            return result
        if binary:
            identity = self.implementation_identity()
            metadata['binary_sha256'] = identity['binary_sha256']
            if identity['reported_version']:
                metadata['reported_version'] = identity['reported_version']
                metadata['browser_version'] = identity['reported_version']
            if identity['version_probe_error']:
                metadata['browser_version_probe_error'] = identity['version_probe_error']
        request = {'engine': engine, 'binary': binary, 'url': case.url,
                   'case_id': case.id,
                   'timeout': timeout, 'output_mode': output_mode,
                   'task_kind': case.task_kind,
                   'resource_detail_links': resource_detail_links(case) if output_mode == 'resource' else False,
                   'browser_args': ['--no-sandbox']
                   if resource_interval is not None and engine == 'chromium' else [],
                   'wait_until': retrieval_plan.wait_until if retrieval_plan else None,
                   'track': case.track,
                   'retrieval_plan': normalized_plan,
                   'include_subdocuments': include_subdocuments,
                   'retrieval_hints': case.retrieval_hints,
                   'allow_private_network': bool(self.config.get('allow_private_network')),
                   'block_private_networks': bool(self.config.get('block_private_networks', False)),
                   'log_level': self.config.get('log_level'),
                   'max_diagnostic_bytes': self.config.get('max_diagnostic_bytes', 100_000_000),
                   'max_original_file_bytes': self.config.get('max_original_file_bytes'),
                   'native_metadata': _worker_native_metadata(self.config.get('native_metadata'))
                   if output_mode == 'markdown' else None,
                   'ca_cert': self.config.get('ca_cert'),
                   'http_proxy': self.config.get('http_proxy'),
                   'dns_mode': self.config.get('dns_mode', 'system'),
                   'native_dns_library': self.config.get('native_dns_library'),
                   'network_policy': self.config.get('network_policy', 'unrestricted'),
                   'endpoint': self.config.get('endpoint',
                   'wss://kitesurf.cloudflare.app/devtools/browser')}
        if engine == 'kitesurf':
            cdp_wait = self.config.get('cdp_wait_until', 'load')
            if cdp_wait not in {'load', 'domcontentloaded', 'networkidle', 'commit'}:
                raise ValueError('Kitesurf cdp_wait_until must be a supported CDP completion event')
            metadata.update(browser='kitesurf', endpoint_route='/browser-run/devtools/browser?browser=kitesurf')
            request.update({
                'remote_markdown_api': output_mode == 'markdown',
                'account_id': self.config['account_id'],
                'api_token': self.config['api_token'],
                'browser': 'kitesurf',
                'wait_until': self.config.get('wait_until', 'networkidle2') if output_mode == 'markdown' else cdp_wait,
                'max_bytes': self.config.get('max_bytes', 20_000_000),
                'endpoint': 'wss://api.cloudflare.com/client/v4/accounts/' + self.config['account_id'] + '/browser-run/devtools/browser?browser=kitesurf',
            })
        if engine == 'chromium' or self.type == 'headless_html':
            configured_package = self.config.get('node_html_markdown_path')
            request['node_html_markdown_path'] = (str(converter_package(configured_package))
                                                 if resource_interval is None else configured_package)
        worker = Path(__file__).with_name('headless_worker.py')
        metadata['worker_sha256'] = hashlib.sha256(worker.read_bytes()).hexdigest()
        metadata['execution_sha256'] = metadata['worker_sha256']
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

    def _run_local_worker(self, worker, request, timeout, limit):
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            command = [sys.executable, str(worker)]
            if request.get('network_policy') == 'loopback_only':
                command = ['/usr/bin/sandbox-exec', '-p', LOOPBACK_SANDBOX_PROFILE, *command]
            with self._process_lock:
                if self._cancel_requested:
                    raise TimeoutError('Isolated fetch cancelled before launch')
            # Popen must not hold the lock needed by the deadline thread.
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                stdout=stdout, stderr=stderr, start_new_session=True)
            with self._process_lock:
                self._active_process = process
                if self._cancel_requested:
                    terminate_owned_process_tree(process)
            stopped = None
            reap_failed = False
            try:
                try:
                    process.stdin.write(json.dumps(request).encode())
                    process.stdin.close()
                except BrokenPipeError:
                    with self._process_lock:
                        if not self._cancel_requested:
                            raise
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    with self._process_lock:
                        cancelled = self._cancel_requested
                    if cancelled:
                        stopped = 'timeout'
                        break
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
                with self._process_lock:
                    if self._cancel_requested:
                        stopped = 'timeout'
                if stopped:
                    terminate_owned_process_tree(process)
                    reap_failed = True
                    await_owned_process_exit(process)
                    reap_failed = False
                stdout_bytes = os.fstat(stdout.fileno()).st_size
                stderr_bytes = os.fstat(stderr.fileno()).st_size
                stdout.seek(0); raw = stdout.read(limit+1)
                stderr.seek(0); diagnostic_raw = stderr.read(limit+1)
                diagnostic = diagnostic_raw[:limit].decode('utf-8', 'replace')
            finally:
                try:
                    # A failed reap already spent the grace and raised.
                    if not reap_failed:
                        terminate_owned_process_tree(process)
                        await_owned_process_exit(process)
                finally:
                    if not process.stdin.closed:
                        try:
                            process.stdin.close()
                        except BrokenPipeError:
                            pass
                    with self._process_lock:
                        if self._active_process is process:
                            self._active_process = None
        return raw, diagnostic, stopped, process.returncode, stdout_bytes, stderr_bytes


@contextmanager
def native_cdp_browser(playwright, request, deadline, metadata=None):
    """Own a fresh native CDP server within the caller's complete deadline."""
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    remaining = lambda: max(.001, deadline - time.monotonic())
    command = [request['binary'], 'serve', '--host', '127.0.0.1', '--port', str(port)]
    engine = request['engine']
    if engine == 'lightpanda':
        command += ['--http-timeout', str(max(1, math.ceil(remaining() * 1000)))]
    elif engine == 'moli':
        if request.get('block_private_networks'): command.append('--block-private-networks')
        if request.get('log_level'): command += ['--log-level', request['log_level']]
    elif engine == 'obscura':
        command.append('--quiet')
        if request.get('allow_private_network'):
            command.append('--allow-private-network')
    else:
        raise ValueError('Unsupported native CDP server engine')
    if engine == 'lightpanda' and request.get('output_mode') in {'resource', 'document_page'}:
        command += ['--load-resources', 'iframe']
    if request.get('http_proxy'):
        command += ['--http-proxy' if engine in {'lightpanda', 'moli'} else '--proxy', request['http_proxy']]
    if request.get('ca_cert') and engine in {'lightpanda', 'moli'}:
        command += ['--ca-cert', str(request['ca_cert'])]
    browser = None
    with tempfile.TemporaryFile() as log:
        environment = {**os.environ, 'SSL_CERT_FILE': str(request['ca_cert'])} if engine == 'obscura' and request.get('ca_cert') else None
        from harness.web_fetch.network_dns import native_process_dns_environment
        with native_process_dns_environment(
            request.get('dns_mode', 'system'),
            request.get('native_dns_library'),
            deadline,
            metadata if metadata is not None else {},
            environment,
        ) as process_environment:
            process = subprocess.Popen(
                command, stdout=log, stderr=log, env=process_environment
            )
            try:
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read().decode('utf-8', 'replace')[:4000])
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Native CDP server startup exceeded the call deadline')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=min(.1, remaining())):
                            break
                    except OSError:
                        time.sleep(min(.02, remaining()))
                browser = playwright.chromium.connect_over_cdp(
                    'http://127.0.0.1:' + str(port), timeout=remaining() * 1000)
                yield browser
            finally:
                try:
                    if browser is not None:
                        browser.close()
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)
                    if engine == 'moli' and metadata is not None:
                        log.seek(0)
                        raw_log = log.read(int(request.get('max_diagnostic_bytes', 100_000_000))+1)
                        metadata.update(_diagnostic_log_b64=base64.b64encode(raw_log).decode('ascii'),
                            diagnostic_log_sha256=hashlib.sha256(raw_log).hexdigest(), diagnostic_log_bytes=len(raw_log),
                            diagnostic_log_process_completed=True)
