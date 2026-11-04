"""Isolated task-native Web Fetch comparison, with no provider fallback."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
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
from harness.platform_capabilities import sandbox_exec_available, sandbox_exec_command
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.providers.runtime_identity import require_expected_provider_versions
from harness.web_fetch.retrieval_hints import native_contract_for_case, plan_for_case

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


def _wait_flag_completion(delay_flag, plan):
    arguments = [
        '--wait-selector' if plan['wait_kind'] == 'selector' else '--wait-script',
        plan['wait_value'],
        '--wait-until',
        plan['wait_until'],
    ]
    if plan['delay_ms']:
        arguments += [delay_flag, str(plan['delay_ms'])]
    return arguments


def _obscura_completion_arguments(plan):
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


def _exact_completion(plan):
    strategy = (
        'frame_selector_count_script'
        if plan.get('frame_selector')
        else 'selector'
        if plan['wait_kind'] == 'selector'
        else 'selector_count_script'
    )
    return {
        'strategy': strategy,
        'completion_support': 'exact',
        'minimum_count_enforced': True,
        'unsupported': [],
    }


def _chromium_completion(plan):
    metadata = _exact_completion(plan)
    if plan.get('frame_selector'):
        # These paths can wait on same-origin child content, but their
        # existing Markdown/outerHTML contract does not serialize it.
        metadata['output_unsupported'] = ['child_frame_serialization']
    return metadata


def _lightpanda_completion(plan):
    if plan['wait_kind'] == 'selector':
        return _exact_completion(plan)
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
    return metadata


def _obscura_completion(plan):
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
    return {
        'strategy': 'fixed_delay' if frame else 'fixed_delay_then_selector',
        'completion_support': 'approximate',
        'minimum_count_enforced': False,
        'unsupported': unsupported,
    }


# Slots of an argument template. Each expands to zero or more arguments that
# depend on the call; every other template token is a literal argument.
_URL = '<url>'
_EXPRESSION = '<expression>'
_TIMEOUT_MS = '<timeout-ms>'
_TIMEOUT_SECONDS = '<timeout-seconds>'
_PROXY = '<proxy>'
_CA_CERT = '<ca-cert>'
_PRIVATE_NETWORK = '<allow-private-network>'
_SUBDOCUMENTS = '<subdocuments>'
_COMPLETION = '<completion>'
_COMPLETION_OR_WAIT = '<completion-or-requested-wait>'
_COMPLETION_OR_DOM_LOADED = '<completion-or-domcontentloaded>'
_BLOCK_PRIVATE_NETWORKS = '<block-private-networks>'
_LOG_LEVEL = '<log-level>'
_PROJECTION_IFRAMES = '<projection-iframes>'

# Metadata values resolved from the call rather than the engine.
_CLI_WAIT = '<cli-wait>'
_CDP_WAIT = '<cdp-wait>'
_CLI_RAW_WAIT = {'file': 'automatic raw response',
                 'document': 'automatic raw response or serialized HTML'}
_NAVIGATION_WAIT = dict.fromkeys(('file', 'document'), 'navigation response bytes')
_ADAPTIVE_WAIT = 'load + adaptive settle'
_PAGE_EXTRACTORS = {'resource': 'rendered_dom_resources',
                    'media_metadata': 'rendered_body_and_standard_media_metadata',
                    'document_page': 'browser_runtime_document_text'}
_CLI_ROUTES = {'html': 'html', 'markdown': 'markdown', 'document': 'raw', 'file': 'raw',
               'resource': 'projection', 'media_metadata': 'projection',
               'document_page': 'projection'}


def _expand(template, slots):
    arguments = []
    for token in template:
        if token in slots:
            arguments += slots[token]
        else:
            arguments.append(token)
    return arguments


@dataclass(frozen=True)
class HostedBackend:
    """A provider-run browser reached through its account API."""
    browser: str
    account_deployment: str
    cdp_deployment: str
    container_deployment: str
    container_identity_deployment: str
    identity_source: str
    identity_endpoint: str
    endpoint_route: str

    def endpoint(self, account_id):
        return 'wss://api.cloudflare.com/client/v4/accounts/' + account_id + self.endpoint_route


@dataclass(frozen=True)
class HeadlessEngine:
    """Everything that differs between engines; defaults describe no capability."""
    # One-shot CLI argument templates by route, after the binary. A route without
    # a template is unavailable for this engine.
    commands: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Native CDP server arguments after `serve --host --port`; None means no server.
    cdp_server_arguments: tuple[str, ...] | None = None
    cdp_ca_certificate_environment: bool = False
    retain_cdp_server_log: bool = False
    proxy_flag: str = '--proxy'
    subdocument_flags: tuple[str, ...] = ()
    subdocument_interface: str | None = None
    subdocument_support: str = 'unsupported'
    native_json_format: str | None = None
    native_eval_format: str | None = None
    completion_arguments: Callable[[dict], list[str]] | None = None
    completion_semantics: Callable[[dict], dict] | None = None
    # Launched through Playwright, which owns its revision, DNS profile and DOM conversion.
    playwright_launched: bool = False
    container_browser_args: tuple[str, ...] = ()
    version_args: tuple[str, ...] | None = None
    hosted: HostedBackend | None = None
    # Native Markdown for documents and webpage media: under every provider type,
    # or only under headless_markdown.
    rich_task_markdown: bool = False
    rich_task_markdown_any_type: bool = False
    document_markdown: bool = False
    video_body_markdown: bool = False
    wait_until: Mapping[str, str] = field(default_factory=dict)
    default_wait_until: str = 'load'
    file_extractor: str = ''
    markdown_extractor: str = ''


ENGINES = {
    'kitesurf': HeadlessEngine(
        hosted=HostedBackend(
            browser='kitesurf',
            account_deployment='Cloudflare Browser Run account API',
            cdp_deployment='public cloud CDP',
            container_deployment='Cloudflare Browser Run from isolated client container',
            container_identity_deployment=(
                'Cloudflare Browser Run account API from isolated client container'),
            identity_source=('provider product and endpoint observed before calls; '
                             'hosted backend version is not exposed'),
            identity_endpoint='Browser Rendering /markdown with browser=kitesurf',
            endpoint_route='/browser-run/devtools/browser?browser=kitesurf',
        ),
        rich_task_markdown=True,
        wait_until={**_NAVIGATION_WAIT, 'markdown': 'networkidle2'},
        default_wait_until=_CDP_WAIT,
        file_extractor='kitesurf_cdp_response',
        markdown_extractor='cloudflare_native_markdown',
    ),
    'lightpanda': HeadlessEngine(
        commands={
            'html': ('fetch', '--dump', 'html', _PROXY, _COMPLETION_OR_DOM_LOADED,
                     '--http-timeout', _TIMEOUT_MS, _URL),
            'markdown': ('fetch', '--dump', 'markdown', _SUBDOCUMENTS, _PROXY,
                         _COMPLETION_OR_WAIT, _CA_CERT, '--terminate-ms', _TIMEOUT_MS,
                         '--http-timeout', _TIMEOUT_MS, _URL),
        },
        cdp_server_arguments=('--http-timeout', _TIMEOUT_MS, _PROJECTION_IFRAMES,
                              _PROXY, _CA_CERT),
        proxy_flag='--http-proxy',
        subdocument_flags=('--with-frames', '--load-resources', 'iframe'),
        subdocument_interface='--load-resources iframe + --with-frames',
        subdocument_support='native_markdown_omits_child_document',
        native_json_format='lightpanda_json',
        completion_arguments=partial(_wait_flag_completion, '--wait-ms'),
        completion_semantics=_lightpanda_completion,
        version_args=('version',),
        rich_task_markdown=True,
        wait_until={**_NAVIGATION_WAIT, 'markdown': _CLI_WAIT},
        file_extractor='lightpanda_cdp_response',
        markdown_extractor='lightpanda_native_markdown',
    ),
    'obscura': HeadlessEngine(
        commands={
            'html': ('fetch', '--dump', 'html', _PROXY, _COMPLETION_OR_DOM_LOADED,
                     '--timeout', _TIMEOUT_SECONDS, '--quiet', _URL),
            # Its documented default is load plus an adaptive, at-most
            # five-second quiescence wait. Do not replace it with a fixed delay.
            'markdown': (_PRIVATE_NETWORK, _PROXY, 'fetch', '--dump', 'markdown', _COMPLETION,
                         '--timeout', _TIMEOUT_SECONDS, '--quiet', _URL),
            'raw': ('fetch', '--dump', 'original', _PRIVATE_NETWORK, _PROXY,
                    '--timeout', _TIMEOUT_SECONDS, '--quiet', _URL),
            'projection': ('fetch', '--eval', _EXPRESSION, '--timeout', _TIMEOUT_SECONDS,
                           '--quiet', _PRIVATE_NETWORK, _PROXY, _URL),
            'native_eval': (_PRIVATE_NETWORK, _PROXY, 'fetch', '--eval', _EXPRESSION,
                            '--timeout', _TIMEOUT_SECONDS, '--quiet', _URL),
        },
        cdp_server_arguments=('--quiet', _PRIVATE_NETWORK, _PROXY),
        cdp_ca_certificate_environment=True,
        native_eval_format='obscura_eval',
        completion_arguments=_obscura_completion_arguments,
        completion_semantics=_obscura_completion,
        rich_task_markdown=True,
        wait_until={**_CLI_RAW_WAIT, 'markdown': _ADAPTIVE_WAIT,
                    'resource': _ADAPTIVE_WAIT, 'media_metadata': _ADAPTIVE_WAIT},
        file_extractor='obscura_original_response',
        markdown_extractor='obscura_native_markdown',
    ),
    'moli': HeadlessEngine(
        commands={
            'html': ('fetch', '--dump', 'html', _PROXY, _COMPLETION_OR_DOM_LOADED,
                     '--timeout', _TIMEOUT_MS, '--http-timeout', _TIMEOUT_MS, _URL),
            'markdown': ('fetch', '--dump', 'markdown', _SUBDOCUMENTS, _PROXY,
                         _COMPLETION_OR_WAIT, _CA_CERT, '--timeout', _TIMEOUT_MS,
                         '--http-timeout', _TIMEOUT_MS, _URL),
            # All three measured Moli releases write direct raw responses such as
            # PDF and media bytes when --dump is omitted. Supplying --dump markdown
            # rejects those responses before extraction can begin. Version 1.1.3
            # documents this behavior explicitly; 1.0.6 and 1.1.1 are bound to
            # release binaries and covered by route probes and end-to-end tests.
            'raw': ('fetch', _PROXY, '--timeout', _TIMEOUT_MS,
                    '--http-timeout', _TIMEOUT_MS, _URL),
        },
        cdp_server_arguments=(_BLOCK_PRIVATE_NETWORKS, _LOG_LEVEL, _PROXY, _CA_CERT),
        retain_cdp_server_log=True,
        proxy_flag='--http-proxy',
        subdocument_flags=('--with-frames',),
        subdocument_interface='--with-frames',
        subdocument_support='native',
        completion_arguments=partial(_wait_flag_completion, '--delay-ms'),
        completion_semantics=_exact_completion,
        document_markdown=True,
        video_body_markdown=True,
        wait_until={**_CLI_RAW_WAIT, 'markdown': _CLI_WAIT},
        file_extractor='moli_cdp_response',
        markdown_extractor='moli_native_markdown',
    ),
    'chromium': HeadlessEngine(
        completion_semantics=_chromium_completion,
        playwright_launched=True,
        container_browser_args=('--no-sandbox',),
        rich_task_markdown_any_type=True,
        wait_until=dict.fromkeys(('file', 'document'),
                                 'browser response bytes or completed native download'),
        file_extractor='browser_native_response_or_download',
        markdown_extractor='node-html-markdown',
    ),
}
_UNREGISTERED_ENGINE = HeadlessEngine()


def _engine_definition(engine):
    """Unregistered names remain valid report labels but select no capability."""
    return ENGINES.get(engine, _UNREGISTERED_ENGINE) if isinstance(engine, str) else _UNREGISTERED_ENGINE


def _resolve_binary(engine, configured):
    # An explicitly expanded empty environment variable is a configuration
    # error.  It must not behave like an omitted option and silently select a
    # different Playwright browser during a partial rerun.
    if configured == '':
        return None
    resolved = shutil.which(configured or engine)
    if (resolved or not _engine_definition(engine).playwright_launched
            or configured not in (None, '', engine)):
        return resolved
    # Playwright owns the Chromium revision used by this adapter. Requiring a
    # second PATH alias can silently select a different system browser.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        bundled = playwright.chromium.executable_path
    return bundled if Path(bundled).is_file() else None


def completion_metadata(engine, plan, output_mode):
    """Describe the exact native semantics used for one normalized plan."""
    if not plan:
        return None
    semantics = _engine_definition(engine).completion_semantics
    if output_mode in {'document', 'file'}:
        metadata = {
            'strategy': 'raw_response',
            'completion_support': 'unsupported',
            'minimum_count_enforced': False,
            'unsupported': ['selector', 'minimum_count', 'post_completion_delay'],
        }
    elif semantics:
        metadata = semantics(plan)
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
    definition = _engine_definition(engine)
    slots = {
        _URL: [url],
        _TIMEOUT_MS: [str(max(1, math.ceil(timeout * 1000)))],
        _TIMEOUT_SECONDS: [str(math.ceil(timeout))],
        _PROXY: [definition.proxy_flag, http_proxy] if http_proxy else [],
        _CA_CERT: ['--ca-cert', str(ca_cert)] if ca_cert else [],
        _PRIVATE_NETWORK: ['--allow-private-network'] if allow_private_network else [],
        _SUBDOCUMENTS: list(definition.subdocument_flags) if include_subdocuments else [],
    }
    if native_metadata:
        if output_mode != 'markdown':
            raise ValueError('native metadata requires native Markdown completion')
        native_format = native_metadata.get('format')
        if definition.native_json_format and native_format == definition.native_json_format:
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
        if definition.native_eval_format and native_format == definition.native_eval_format:
            source = _native_script_bytes(native_metadata)
            slots[_EXPRESSION] = ['JSON.stringify({content:(' + source.decode('utf-8')
                                  + '),final_url:location.href,title:document.title})']
            return [binary, *_expand(definition.commands['native_eval'], slots)]
        raise ValueError('unsupported native metadata route')
    route = _CLI_ROUTES.get(output_mode)
    if route is None:
        raise ValueError('Unsupported browser output mode: ' + output_mode)
    if route == 'projection':
        from harness.web_fetch.task_outputs import (
            DOCUMENT_PAGE_EXPRESSION,
            MEDIA_METADATA_EXPRESSION,
            resource_expression,
        )
        slots[_EXPRESSION] = [
            resource_expression(track, detail_links) if output_mode == 'resource'
            else {'media_metadata': MEDIA_METADATA_EXPRESSION,
                  'document_page': DOCUMENT_PAGE_EXPRESSION}[output_mode]]
    template = definition.commands.get(route)
    if template is None:
        raise ValueError({
            'projection': 'Rendered projection requires a native CDP or eval interface',
            'raw': 'Raw document auto-output is only supported by Moli',
            'markdown': 'Not a native Markdown CLI engine: ' + str(engine),
            'html': 'Not a CLI engine: ' + str(engine),
        }[route])
    if route in {'html', 'markdown'}:
        arguments = definition.completion_arguments
        completion = arguments(retrieval_plan) if arguments and retrieval_plan else None
        slots[_COMPLETION] = completion or []
        slots[_COMPLETION_OR_WAIT] = completion or ['--wait-until', wait_until or 'done']
        slots[_COMPLETION_OR_DOM_LOADED] = completion or ['--wait-until', 'domcontentloaded']
    return [binary, *_expand(template, slots)]


def output_mode_for_case(engine, provider_type, case):
    """Choose one product-native route for each task family."""
    definition = _engine_definition(engine)
    if engine in ENGINES and case.task_kind == 'file':
        return 'file'
    if definition.document_markdown and case.track == 'document':
        return 'markdown'
    rich_task_markdown = definition.rich_task_markdown_any_type or (
        definition.rich_task_markdown and provider_type == 'headless_markdown')
    if rich_task_markdown and (
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
        return 'markdown' if definition.video_body_markdown else 'media_metadata'
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
        local = (not _engine_definition(self.config.get('engine')).hosted
                 and self.config.get('resource_sample_interval_ms') is None)
        return 1.0 if local else 0.0

    def validate(self):
        errors = []
        engine = self.config.get('engine')
        definition = _engine_definition(engine)
        requires_credentials = definition.hosted and self.type == 'headless_markdown'
        if definition is _UNREGISTERED_ENGINE:
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
            if definition.hosted and dns_mode != 'system':
                errors.append('hosted Kitesurf owns its DNS environment')
            elif dns_mode == 'public_doh':
                if resource_interval is not None:
                    errors.append('public_doh is unavailable inside resource containers')
                elif not definition.playwright_launched:
                    validate_native_dns_library(self.config.get('native_dns_library'))
        except ValueError as error:
            errors.append(str(error))
        if resource_interval is not None:
            if requires_credentials:
                errors.extend(name for name in ('api_token', 'account_id')
                              if not self.config.get(name))
            if not self.config.get('resource_container_image'):
                errors.append('resource_container_image')
            if not definition.hosted and not self.config.get('resource_binary'):
                errors.append('resource_binary')
            if not errors:
                try:
                    self._resource_runner().validate()
                except Exception as exc:
                    errors.append('docker_cgroup_v2:' + str(exc))
            self._resolved_binary = self.config.get('resource_binary')
            return self._expected_version_errors(errors)
        binary = _resolve_binary(engine, self.config.get('binary'))
        if not definition.hosted and not binary:
            errors.append('binary')
        self._resolved_binary = binary
        if requires_credentials:
            errors.extend(name for name in ('api_token', 'account_id') if not self.config.get(name))
        if self.config.get('network_policy') == 'loopback_only':
            if not sandbox_exec_available():
                errors.append('macos sandbox-exec for loopback_only network policy')
        if definition.playwright_launched or self.type == 'headless_html':
            if importlib.util.find_spec('playwright') is None:
                errors.append('playwright')
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
        definition = _engine_definition(engine)
        hosted = definition.hosted
        version_args = list(definition.version_args) if definition.version_args else None
        if self.config.get('resource_sample_interval_ms') is not None:
            resource = self._resource_runner().implementation_identity(
                self.config.get('resource_binary'),
                version_args=version_args,
            )
            identity = {
                'identity_source': (
                    'immutable container image and executable bytes observed before calls'
                ),
                'engine': engine,
                'deployment': (
                    hosted.container_identity_deployment
                    if hosted else 'isolated Linux client container'
                ),
                **resource,
            }
            self._implementation_identity = identity
            return dict(identity)
        if hosted:
            identity = {
                'identity_source': hosted.identity_source,
                'engine': engine,
                'deployment': hosted.account_deployment,
                'endpoint': hosted.identity_endpoint,
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
            version = subprocess.run([binary, *(version_args or ['--version'])],
                                     capture_output=True, text=True,
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
        definition = _engine_definition(engine)
        if definition is _UNREGISTERED_ENGINE:
            raise ValueError('unsupported headless engine: ' + str(engine))
        hosted = definition.hosted
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
        route_wait = definition.wait_until.get(output_mode, definition.default_wait_until)
        wait_until = {_CLI_WAIT: cli_wait,
                      _CDP_WAIT: self.config.get('cdp_wait_until', 'load')}.get(route_wait, route_wait)
        extractor = {'file': definition.file_extractor,
                     'document': definition.file_extractor + '+document_text',
                     'markdown': definition.markdown_extractor,
                     **_PAGE_EXTRACTORS}.get(output_mode, 'node-html-markdown')
        output_contract = ('original_file_bytes' if output_mode == 'file' else
                           'readable_document_text' if output_mode in {'document','document_page'} else
                           'structured_resource_urls' if output_mode == 'resource' else
                           'readable_media_metadata' if output_mode == 'media_metadata' else 'markdown')
        metadata = {'engine': engine, 'wait_until': wait_until,
                    'extractor': extractor, 'output_contract': output_contract,
                    'dns_mode': self.config.get('dns_mode', 'system'),
                    'session': 'fresh per call',
                    'network_policy': self.config.get('network_policy', 'unrestricted'),
                    'deployment': (hosted.container_deployment
                                   if resource_interval is not None and hosted
                                   else 'isolated Linux client container'
                                   if resource_interval is not None
                                   else hosted.cdp_deployment if hosted
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
        if normalized_plan and not hosted:
            metadata['completion'] = completion_metadata(engine, normalized_plan, output_mode)
            metadata['completion']['effective_route_timeout_ms'] = round(
                min(timeout, retrieval_plan.timeout_ms / 1000) * 1000
                if retrieval_plan else timeout * 1000
            )
        if include_subdocuments:
            interface = definition.subdocument_interface if output_mode == 'markdown' else None
            metadata['subdocuments'] = {
                'requested': True,
                'support': definition.subdocument_support if interface else 'unsupported',
                'interface': interface,
                'harness_content_assembly': False,
            }
        if hosted and output_mode == 'markdown' and resource_interval is None:
            cloudflare = CloudflareMarkdownProvider(self.name, {
                'account_id': self.config['account_id'], 'api_token': self.config['api_token'],
                'browser': hosted.browser, 'wait_until': self.config.get('wait_until', 'networkidle2'),
                'max_bytes': self.config.get('max_bytes', 20_000_000),
                'page_output': 'native_markdown',
                'uniform_native_parameters': self.config.get('uniform_native_parameters', False),
                'endpoint': hosted.endpoint(self.config['account_id']),
            })
            result = cloudflare.fetch(case, timeout)
            result.metadata = {**metadata, **result.metadata,
                'deployment': hosted.account_deployment}
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
                   'browser_args': list(definition.container_browser_args)
                   if resource_interval is not None else [],
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
        if hosted:
            cdp_wait = self.config.get('cdp_wait_until', 'load')
            if cdp_wait not in {'load', 'domcontentloaded', 'networkidle', 'commit'}:
                raise ValueError('Kitesurf cdp_wait_until must be a supported CDP completion event')
            metadata.update(browser=hosted.browser, endpoint_route=hosted.endpoint_route)
            request.update({
                'remote_markdown_api': output_mode == 'markdown',
                'account_id': self.config['account_id'],
                'api_token': self.config['api_token'],
                'browser': hosted.browser,
                'wait_until': self.config.get('wait_until', 'networkidle2') if output_mode == 'markdown' else cdp_wait,
                'max_bytes': self.config.get('max_bytes', 20_000_000),
                'endpoint': hosted.endpoint(self.config['account_id']),
            })
        if definition.playwright_launched or self.type == 'headless_html':
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
                command = [*sandbox_exec_command(LOOPBACK_SANDBOX_PROFILE), *command]
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


NATIVE_CDP_PORT_ATTEMPTS = 3
_NATIVE_CDP_HOST = '127.0.0.1'
_MACOS_LSOF = '/usr/sbin/lsof'
_LINUX_TCP_LISTEN = '0A'
_ADDRESS_IN_USE = re.compile(rb'address\s*(?:is\s*)?(?:already\s*)?in\s*use', re.I)


def _native_cdp_attempt_output(log, start):
    """Return what the server wrote since ``start``; the child shares the file offset, so it stays put."""
    return os.pread(log.fileno(), os.fstat(log.fileno()).st_size - start, start)


def _linux_listens_on(pid, port):
    """Join the process's socket descriptors to the listening rows of its network namespace."""
    try:
        with open(f'/proc/{pid}/net/tcp', encoding='ascii') as table:
            rows = [row.split() for row in table.readlines()[1:]]
        descriptors = os.listdir(f'/proc/{pid}/fd')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        # A process that is exiting can still show its socket table after root takes over its fd directory.
        return False
    listening = set()
    for row in rows:
        address, row_port = row[1].split(':')
        if (row[3] == _LINUX_TCP_LISTEN and int(row_port, 16) == port
                and socket.inet_ntoa(struct.pack('=I', int(address, 16))) == _NATIVE_CDP_HOST):
            listening.add(f'socket:[{row[9]}]')
    for descriptor in descriptors:
        try:
            if os.readlink(f'/proc/{pid}/fd/{descriptor}') in listening:
                return True
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return False


def _macos_listens_on(pid, port, timeout):
    """Ask lsof, because macOS exposes socket owners only through libproc."""
    try:
        listing = subprocess.run(
            [_MACOS_LSOF, '-a', '-nP', '-p', str(pid), f'-iTCP:{port}', '-sTCP:LISTEN', '-Fn'],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return f'n{_NATIVE_CDP_HOST}:{port}' in listing.stdout.splitlines()


def _process_listens_on(pid, port, timeout):
    """Return whether ``pid`` itself holds the listening socket on the exact CDP address.

    The caller has not reaped ``pid``, so the number cannot pass to another process. A
    wildcard listener does not count: a more specific foreign socket could still receive
    the connection on the systems that allow both.
    """
    if sys.platform == 'linux':
        return _linux_listens_on(pid, port)
    return _macos_listens_on(pid, port, timeout)


def _connect_native_cdp(playwright, process, port, deadline):
    """Return a browser once ``process`` holds the port, or ``None`` if it exits first.

    Nothing connects to the port until the kernel reports this process as its owner, and
    the kernel delivers every connection on that address to the owner. If the server
    exits after that report, another process could take the address before the handshake.
    Being alive when the handshake returns shows it held the address throughout, so only
    that browser is returned; any other is discarded before a caller can use it.
    """
    remaining = lambda: max(.001, deadline - time.monotonic())
    while process.poll() is None:
        if time.monotonic() >= deadline:
            raise TimeoutError('Native CDP server startup exceeded the call deadline')
        if _process_listens_on(process.pid, port, remaining()):
            break
        time.sleep(min(.02, remaining()))
    else:
        return None
    browser = playwright.chromium.connect_over_cdp(
        f'http://{_NATIVE_CDP_HOST}:{port}', timeout=remaining() * 1000)
    if process.poll() is None:
        return browser
    browser.close()
    return None


@contextmanager
def native_cdp_browser(playwright, request, deadline, metadata=None):
    """Own a fresh native CDP server within the caller's complete deadline.

    The servers only accept a numeric port and bind it themselves, so another
    process can take the chosen port first. A server that reports this is
    restarted on a new port, and no connection is made until the server is proven
    to hold its port. A server that exits after the caller received a browser
    fails the call, because the browser it was measuring is gone.
    """
    remaining = lambda: max(.001, deadline - time.monotonic())
    definition = _engine_definition(request['engine'])
    if definition.cdp_server_arguments is None:
        raise ValueError('Unsupported native CDP server engine')
    command = _expand(definition.cdp_server_arguments, {
        _TIMEOUT_MS: [str(max(1, math.ceil(remaining() * 1000)))],
        _BLOCK_PRIVATE_NETWORKS: (
            ['--block-private-networks'] if request.get('block_private_networks') else []),
        _LOG_LEVEL: ['--log-level', request['log_level']] if request.get('log_level') else [],
        _PRIVATE_NETWORK: (
            ['--allow-private-network'] if request.get('allow_private_network') else []),
        _PROJECTION_IFRAMES: (
            ['--load-resources', 'iframe']
            if request.get('output_mode') in {'resource', 'document_page'} else []),
        _PROXY: [definition.proxy_flag, request['http_proxy']] if request.get('http_proxy') else [],
        _CA_CERT: ['--ca-cert', str(request['ca_cert'])] if request.get('ca_cert') else [],
    })
    browser = None
    with tempfile.TemporaryFile() as log:
        environment = ({**os.environ, 'SSL_CERT_FILE': str(request['ca_cert'])}
                       if definition.cdp_ca_certificate_environment and request.get('ca_cert')
                       else None)
        from harness.web_fetch.network_dns import native_process_dns_environment
        with native_process_dns_environment(
            request.get('dns_mode', 'system'),
            request.get('native_dns_library'),
            deadline,
            metadata if metadata is not None else {},
            environment,
        ) as process_environment:
            process = None
            try:
                for attempt in range(1, NATIVE_CDP_PORT_ATTEMPTS + 1):
                    with socket.socket() as candidate:
                        candidate.bind((_NATIVE_CDP_HOST, 0))
                        port = candidate.getsockname()[1]
                    log_start = os.fstat(log.fileno()).st_size
                    process = subprocess.Popen(
                        [request['binary'], 'serve', '--host', _NATIVE_CDP_HOST, '--port', str(port), *command],
                        stdout=log, stderr=log, env=process_environment,
                    )
                    browser = _connect_native_cdp(playwright, process, port, deadline)
                    if browser is not None:
                        break
                    output = _native_cdp_attempt_output(log, log_start)
                    if attempt == NATIVE_CDP_PORT_ATTEMPTS or not _ADDRESS_IN_USE.search(output):
                        raise RuntimeError(output.decode('utf-8', 'replace')[:4000]
                                           or f'Native CDP server exited with code {process.returncode} and no output')
                yield browser
                # Nothing stops the server while the caller runs, so an exit here is a server failure.
                if process.poll() is not None:
                    output = _native_cdp_attempt_output(log, log_start).decode('utf-8', 'replace')[:4000]
                    raise RuntimeError(
                        f'Native CDP server exited with code {process.returncode} while its browser '
                        f'was in use: {output or "no output"}')
            finally:
                try:
                    if browser is not None:
                        browser.close()
                finally:
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1)
                    if process is not None and definition.retain_cdp_server_log and metadata is not None:
                        log.seek(0)
                        raw_log = log.read(int(request.get('max_diagnostic_bytes', 100_000_000))+1)
                        metadata.update(_diagnostic_log_b64=base64.b64encode(raw_log).decode('ascii'),
                            diagnostic_log_sha256=hashlib.sha256(raw_log).hexdigest(), diagnostic_log_bytes=len(raw_log),
                            diagnostic_log_process_completed=True)
