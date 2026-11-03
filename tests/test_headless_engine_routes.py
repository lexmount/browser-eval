"""Pin every command line and route each headless engine produces.

The snapshot was recorded from the if-chain implementation before engines moved
into a registry, so it proves that the registry keeps the same behaviour. Each
line holds one distinct output and the scenarios that produce it. Long browser
expressions are reduced to their SHA-256. Set ``UPDATE_GOLDEN=1`` only after an
intended route change, and explain the change in the pull request.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harness.web_fetch.providers.headless import (
    HeadlessProvider,
    _resolve_binary,
    cli_command,
    completion_metadata,
    native_cdp_browser,
    output_mode_for_case,
)
from lexbench_webfetch.models import EvalCase

GOLDEN = Path(__file__).parent / 'golden' / 'headless-engine-routes.jsonl'
HEADLESS = 'harness.web_fetch.providers.headless'
URL = 'https://example.com/page'
# Spelled out so that adding or removing a registered engine changes this test.
ENGINE_NAMES = ('kitesurf', 'lightpanda', 'obscura', 'moli', 'chromium')
OUTPUT_MODES = ('html', 'markdown', 'document', 'file', 'resource', 'media_metadata',
                'document_page', 'unsupported-mode')
PROVIDER_TYPES = ('headless_markdown', 'headless_html')
ALL_NETWORK_OPTIONS = {'allow_private_network': True, 'ca_cert': '/fixture/ca.pem',
                       'http_proxy': 'http://127.0.0.1:8123', 'include_subdocuments': True}


def _plan(**changes):
    return {
        'version': 3, 'wait_until': 'load', 'selector': '.ready', 'minimum_count': 1,
        'timeout_ms': 45000, 'delay_ms': 0, 'frame_selector': None,
        'minimum_text_fragments': 0, 'wait_kind': 'selector', 'wait_value': '.ready',
        **changes,
    }


COUNT_SCRIPT = '() => document.querySelectorAll("li").length >= 5'
SCRIPT_WAIT = {'wait_kind': 'script', 'wait_value': COUNT_SCRIPT, 'selector': 'li'}
PLANS = {
    'selector': _plan(),
    'selector-delay': _plan(delay_ms=1500),
    'networkidle': _plan(wait_until='networkidle'),
    'script': _plan(**SCRIPT_WAIT, minimum_count=5),
    'script-delay': _plan(**SCRIPT_WAIT, minimum_count=5, delay_ms=2500),
    'script-text': _plan(**SCRIPT_WAIT, minimum_count=5, minimum_text_fragments=3),
    'frame': _plan(**SCRIPT_WAIT, frame_selector='iframe'),
}
COMMAND_OPTIONS = {
    'base': {},
    'whole-second-timeout': {'timeout': 45},
    'private-network': {'allow_private_network': True},
    'ca-cert': {'ca_cert': '/fixture/ca.pem'},
    'proxy': {'http_proxy': 'http://127.0.0.1:8123'},
    'subdocuments': {'include_subdocuments': True},
    'wait-until': {'wait_until': 'networkidle'},
    'image-resource': {'track': 'image', 'detail_links': True},
    'video-resource': {'track': 'video'},
    'all-network': ALL_NETWORK_OPTIONS,
    **{f'plan-{name}': {'retrieval_plan': plan} for name, plan in PLANS.items()},
    'plan-all-network': {'retrieval_plan': PLANS['selector-delay'], **ALL_NETWORK_OPTIONS},
}
EVAL_SOURCE = '() => document.body.innerText'
NATIVE_METADATA = {
    'lightpanda-json': {'format': 'lightpanda_json'},
    'obscura-eval': {'format': 'obscura_eval', 'script_source': EVAL_SOURCE,
                     'script_sha256': hashlib.sha256(EVAL_SOURCE.encode()).hexdigest()},
    'unknown-format': {'format': 'other'},
}


def _printable(argument):
    return argument if len(argument) < 120 else (
        '<sha256:' + hashlib.sha256(argument.encode()).hexdigest() + '>')


def _command(engine, mode, timeout=1.5, **options):
    try:
        return [_printable(part) for part in cli_command(
            engine, '/binary', URL, timeout, mode, **options)]
    except ValueError as error:
        return 'ValueError: ' + str(error)


def _cli_commands():
    rows = {}
    for engine in (*ENGINE_NAMES, 'unknown'):
        for mode in OUTPUT_MODES:
            for label, options in COMMAND_OPTIONS.items():
                rows[f'{engine}/{mode}/{label}'] = _command(engine, mode, **options)
        for label, metadata in NATIVE_METADATA.items():
            for variant, options in {
                'base': {}, 'plan': {'retrieval_plan': PLANS['selector']},
                'all-network': ALL_NETWORK_OPTIONS,
            }.items():
                rows[f'{engine}/native-metadata/{label}/{variant}'] = _command(
                    engine, 'markdown', native_metadata=metadata, **options)
            rows[f'{engine}/native-metadata/{label}/html-mode'] = _command(
                engine, 'html', native_metadata=metadata)
    return rows


class _ServerStarted(Exception):
    pass


def _server_command(engine, options):
    popen = MagicMock(side_effect=_ServerStarted)
    with patch(HEADLESS + '.subprocess.Popen', popen), \
         patch(HEADLESS + '.time.monotonic', return_value=100.0):
        try:
            with native_cdp_browser(MagicMock(), {
                'engine': engine, 'binary': '/binary', **options,
            }, 145.0):
                pytest.fail('The server must not start in this fixture')
        except _ServerStarted:
            pass
        except ValueError as error:
            return 'ValueError: ' + str(error)
    argv = list(popen.call_args.args[0])
    argv[argv.index('--port') + 1] = '<port>'
    environment = popen.call_args.kwargs.get('env') or {}
    return {'argv': argv, 'SSL_CERT_FILE': environment.get('SSL_CERT_FILE')}


def _server_commands():
    variants = {
        'base': {},
        'proxy': {'http_proxy': 'http://127.0.0.1:8123'},
        'ca-cert': {'ca_cert': '/fixture/ca.pem'},
        'private-network': {'allow_private_network': True, 'block_private_networks': True},
        'log-level': {'log_level': 'debug'},
        'resource': {'output_mode': 'resource'},
        'document-page': {'output_mode': 'document_page'},
        'file': {'output_mode': 'file'},
        # One option at a time cannot pin the relative order of the template slots.
        'all-options': {'http_proxy': 'http://127.0.0.1:8123', 'ca_cert': '/fixture/ca.pem',
                        'allow_private_network': True, 'block_private_networks': True,
                        'log_level': 'debug', 'output_mode': 'resource'},
    }
    return {f'{engine}/{label}': _server_command(engine, options)
            for engine in (*ENGINE_NAMES, 'unknown') for label, options in variants.items()}


def _completion():
    rows = {}
    for engine in (*ENGINE_NAMES, 'unknown'):
        for mode in ('markdown', 'html', 'document', 'file', 'resource'):
            rows[f'{engine}/{mode}/no-plan'] = completion_metadata(engine, None, mode)
            for label, plan in PLANS.items():
                try:
                    rows[f'{engine}/{mode}/{label}'] = completion_metadata(engine, plan, mode)
                except ValueError as error:
                    rows[f'{engine}/{mode}/{label}'] = 'ValueError: ' + str(error)
    return rows


def _case(identifier, track, task_kind, url=URL, **fields):
    return EvalCase(identifier, identifier, url, 'public', [], track=track,
                    task_kind=task_kind, **fields)


CASES = {
    'web-body': _case('web-body', 'web', 'body'),
    'web-file': _case('web-file', 'web', 'file'),
    'file-file': _case('file-file', 'file', 'file'),
    'document-body-pdf': _case('document-body-pdf', 'document', 'body', URL + '.pdf'),
    'document-body-html': _case('document-body-html', 'document', 'body', URL + '.html'),
    'document-body-directory': _case('document-body-directory', 'document', 'body', URL + '/'),
    'image-resource': _case('image-resource', 'image', 'resource'),
    'video-resource': _case('video-resource', 'video', 'resource'),
    'image-body': _case('image-body', 'image', 'body'),
    'video-body': _case('video-body', 'video', 'body'),
    'articles-structure': _case('articles-structure', 'articles', 'structure'),
    'dynamic-hinted': _case('dynamic-hinted', 'dynamic', 'body', retrieval_hints={
        'version': 1, 'wait_until': 'load', 'completion': {
            'selector': '.ready', 'minimum_count': 2, 'timeout_ms': 30000, 'delay_ms': 1000},
    }),
    'challenge-subdocuments': _case('challenge-subdocuments', 'challenge', 'body'),
}


def _output_modes():
    return {f'{engine}/{provider_type}/{label}': output_mode_for_case(engine, provider_type, case)
            for engine in (*ENGINE_NAMES, 'unknown')
            for provider_type in PROVIDER_TYPES
            for label, case in CASES.items()}


CONFIG = {'binary': '/binary', 'account_id': 'account', 'api_token': 'placeholder',
          'include_subdocuments_by_track': {'challenge': True},
          'resource_container_image': 'image', 'resource_binary': '/resource-binary',
          'node_html_markdown_path': '/fixture/converter'}
REQUEST_FIELDS = ('binary', 'output_mode', 'browser_args', 'wait_until', 'include_subdocuments',
                  'remote_markdown_api', 'browser', 'max_bytes')
METADATA_FIELDS = ('wait_until', 'extractor', 'output_contract', 'deployment',
                   'browser', 'endpoint_route', 'subdocuments')
DEFAULT_ENDPOINT = 'wss://kitesurf.cloudflare.app/devtools/browser'


def _fetch_route(provider, case):
    identity = {'binary_sha256': 'fixture', 'reported_version': 'fixture 1',
                'version_probe_error': None}
    raw = json.dumps({'ok': True, 'content': 'body', 'metadata': {}}).encode()
    requests = []

    def local_worker(_worker, request, *_):
        requests.append(request)
        return raw, '', None, 0, len(raw), 0

    def container_worker(request, **_):
        requests.append(request)
        return MagicMock(stdout=raw, stderr=b'', exit_code=0, resource={}, environment={})

    runner = MagicMock()
    runner.run.side_effect = container_worker
    cloudflare = MagicMock()
    cloudflare.return_value.fetch.return_value = MagicMock(metadata={})
    with patch(HEADLESS + '._resolve_binary', return_value='/binary'), \
         patch(HEADLESS + '.converter_package', return_value=Path('/converter')), \
         patch(HEADLESS + '.CloudflareMarkdownProvider', cloudflare), \
         patch.object(provider, 'implementation_identity', return_value=identity), \
         patch.object(provider, '_resource_runner', return_value=runner), \
         patch.object(provider, '_run_local_worker', local_worker):
        result = provider.fetch(case, 45)
    row = {'metadata': {field: result.metadata[field] for field in METADATA_FIELDS
                        if field in result.metadata},
           'completion_recorded': 'completion' in result.metadata}
    if cloudflare.called:
        row['cloudflare'] = {key: value for key, value in cloudflare.call_args.args[1].items()
                             if key != 'api_token'}
    else:
        row['request'] = {field: requests[0][field] for field in REQUEST_FIELDS
                          if field in requests[0]}
        row['request_converter'] = requests[0].get('node_html_markdown_path')
        if requests[0]['endpoint'] != DEFAULT_ENDPOINT:
            row['request_endpoint'] = requests[0]['endpoint']
    return row


def _fetch_routes():
    deployments = {'local': {}, 'resource': {'resource_sample_interval_ms': 50}}
    rows = {}
    for engine in ENGINE_NAMES:
        for provider_type in PROVIDER_TYPES:
            for deployment, changes in deployments.items():
                provider = HeadlessProvider(engine, {
                    **CONFIG, 'type': provider_type, 'engine': engine, **changes})
                for label, case in CASES.items():
                    rows[f'{engine}/{provider_type}/{deployment}/{label}'] = _fetch_route(
                        provider, case)
        provider = HeadlessProvider(engine, {
            **CONFIG, 'type': 'headless_html', 'engine': engine, 'cdp_wait_until': 'networkidle'})
        rows[f'{engine}/headless_html/cdp-wait/web-body'] = _fetch_route(
            provider, CASES['web-body'])
    return rows


def _reject_native_dns(_library):
    raise ValueError('native DNS library checked')


def _validate_with_missing_dependencies(provider):
    with patch(HEADLESS + '._resolve_binary', return_value='/binary'), \
         patch(HEADLESS + '.importlib.util.find_spec', return_value=None), \
         patch(HEADLESS + '.shutil.which', return_value=None), \
         patch(HEADLESS + '.require_converter', side_effect=ValueError('converter checked')), \
         patch(HEADLESS + '.sandbox_exec_available', return_value=False), \
         patch(HEADLESS + '.validate_native_dns_library', _reject_native_dns), \
         patch.object(provider, '_resource_runner'):
        return provider.validate()


def _validate_without_binary(provider):
    with patch(HEADLESS + '._resolve_binary', return_value=None), \
         patch(HEADLESS + '.importlib.util.find_spec', return_value=object()), \
         patch(HEADLESS + '.shutil.which', return_value='/node'), \
         patch(HEADLESS + '.require_converter'):
        return provider.validate()


def _validation():
    variants = {
        'base': {},
        'without-credentials': {'account_id': None, 'api_token': None},
        'public-doh': {'dns_mode': 'public_doh'},
        'resource': {'resource_sample_interval_ms': 50},
        'resource-without-binary': {'resource_sample_interval_ms': 50, 'resource_binary': None},
        'resource-public-doh': {'resource_sample_interval_ms': 50, 'dns_mode': 'public_doh'},
        'native-lightpanda': {'native_metadata': NATIVE_METADATA['lightpanda-json']},
        'native-obscura': {'native_metadata': NATIVE_METADATA['obscura-eval']},
        'loopback': {'network_policy': 'loopback_only'},
    }
    rows = {}
    for engine in (*ENGINE_NAMES, 'unknown'):
        for provider_type in PROVIDER_TYPES:
            base = {'type': provider_type, 'engine': engine}
            for label, changes in variants.items():
                rows[f'{engine}/{provider_type}/{label}'] = _validate_with_missing_dependencies(
                    HeadlessProvider(engine, {**CONFIG, **base, **changes}))
            rows[f'{engine}/{provider_type}/missing-binary'] = _validate_without_binary(
                HeadlessProvider(engine, base))
    return rows


def _identities():
    rows = {}
    binary = str(Path(__file__))
    for engine in ENGINE_NAMES:
        run = MagicMock(return_value=MagicMock(returncode=0, stdout='fixture 1\n', stderr=''))
        provider = HeadlessProvider(engine, {'engine': engine, 'binary': binary})
        with patch(HEADLESS + '._resolve_binary', return_value=binary), \
             patch(HEADLESS + '.subprocess.run', run):
            identity = provider.implementation_identity()
        identity.pop('binary_sha256')
        rows[f'{engine}/local'] = {
            **identity, 'version_argv': run.call_args.args[0][1:] if run.called else None}
        resource = MagicMock()
        resource.implementation_identity.return_value = {'binary_sha256': None}
        provider = HeadlessProvider(engine, {
            'engine': engine, 'resource_sample_interval_ms': 50,
            'resource_binary': '/resource-binary', 'resource_container_image': 'image',
        })
        with patch.object(provider, '_resource_runner', return_value=resource):
            identity = provider.implementation_identity()
        rows[f'{engine}/resource'] = {
            **identity,
            'version_args': resource.implementation_identity.call_args.kwargs['version_args'],
        }
    return rows


def _cancellation():
    return {f'{engine}/{label}': HeadlessProvider(
                engine, {'engine': engine, **config}).cancellation_cleanup_seconds
            for engine in (*ENGINE_NAMES, 'unknown')
            for label, config in {'local': {},
                                  'resource': {'resource_sample_interval_ms': 50}}.items()}


def _write_snapshot(sections):
    """Write one line per distinct output so a route change is one reviewable line."""
    lines = []
    for section, rows in sorted(sections.items()):
        groups = {}
        for key, value in rows.items():
            groups.setdefault(json.dumps(value, ensure_ascii=False, sort_keys=True), []).append(key)
        lines += [json.dumps({'section': section, 'output': json.loads(output), 'scenarios': keys},
                             ensure_ascii=False, sort_keys=True)
                  for output, keys in sorted(groups.items())]
    GOLDEN.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _read_snapshot():
    sections = {}
    for line in GOLDEN.read_text(encoding='utf-8').splitlines():
        group = json.loads(line)
        rows = sections.setdefault(group['section'], {})
        rows.update(dict.fromkeys(group['scenarios'], group['output']))
    return sections


def test_every_engine_route_matches_the_recorded_snapshot():
    actual = json.loads(json.dumps({
        'cancellation_cleanup_seconds': _cancellation(),
        'cli_commands': _cli_commands(),
        'completion_metadata': _completion(),
        'fetch_routes': _fetch_routes(),
        'implementation_identity': _identities(),
        'native_cdp_servers': _server_commands(),
        'output_modes': _output_modes(),
        'validation': _validation(),
    }))
    if os.environ.get('UPDATE_GOLDEN') == '1':
        _write_snapshot(actual)
        return
    expected = _read_snapshot()
    assert actual.keys() == expected.keys()
    differences = [f'{section}/{key}: {expected[section].get(key)!r} -> {rows.get(key)!r}'
                   for section, rows in actual.items()
                   for key in sorted(rows.keys() | expected[section].keys())
                   if rows.get(key) != expected[section].get(key)]
    assert not differences, '\n'.join(differences[:40])


@pytest.mark.parametrize(('engine', 'mode', 'options', 'expected'), [
    ('moli', 'markdown', {}, ['/binary', 'fetch', '--dump', 'markdown', '--wait-until', 'done',
                              '--timeout', '1500', '--http-timeout', '1500', URL]),
    ('moli', 'file', {}, ['/binary', 'fetch', '--timeout', '1500', '--http-timeout', '1500', URL]),
    ('lightpanda', 'markdown', {}, ['/binary', 'fetch', '--dump', 'markdown',
                                    '--wait-until', 'done', '--terminate-ms', '1500',
                                    '--http-timeout', '1500', URL]),
    ('lightpanda', 'html', {}, ['/binary', 'fetch', '--dump', 'html',
                                '--wait-until', 'domcontentloaded', '--http-timeout', '1500', URL]),
    ('obscura', 'markdown', {}, ['/binary', 'fetch', '--dump', 'markdown',
                                 '--timeout', '2', '--quiet', URL]),
    ('obscura', 'file', {'allow_private_network': True, 'http_proxy': 'http://proxy'},
     ['/binary', 'fetch', '--dump', 'original', '--allow-private-network',
      '--proxy', 'http://proxy', '--timeout', '2', '--quiet', URL]),
])
def test_representative_command_lines_stay_readable_in_review(engine, mode, options, expected):
    assert cli_command(engine, '/binary', URL, 1.5, mode, **options) == expected


def test_only_chromium_falls_back_to_the_playwright_browser(tmp_path):
    bundled = tmp_path / 'chrome'
    bundled.write_bytes(b'')
    playwright = MagicMock()
    playwright.return_value.__enter__.return_value.chromium.executable_path = str(bundled)
    with patch(HEADLESS + '.shutil.which', return_value=None), \
         patch('playwright.sync_api.sync_playwright', playwright):
        assert _resolve_binary('chromium', None) == str(bundled)
        assert _resolve_binary('chromium', 'chromium') == str(bundled)
        assert playwright.call_count == 2
        assert _resolve_binary('chromium', '') is None
        assert _resolve_binary('chromium', '/missing') is None
        for engine in ('moli', 'lightpanda', 'obscura', 'kitesurf', 'unknown'):
            assert _resolve_binary(engine, None) is None
        assert playwright.call_count == 2
