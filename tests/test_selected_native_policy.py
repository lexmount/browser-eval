"""Offline behavior checks for the selected ten-method configuration."""
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch, Mock

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.cli import build_parser
from harness.web_fetch.providers.base import build_provider
from harness.web_fetch.native_routes import normalized_http_parameters, verify_saved_native_answer
from harness.web_fetch.runner import run_evaluation

SELECTED = ('moli-webfetch', 'firecrawl', 'cloudflare-markdown', 'tavily', 'exa',
            'brightdata', 'browser-use-fetch', 'browserbase', 'deepseek-fetch-api', 'crawl4ai-cloud')
CONFIG = json.loads(Path('configs/providers.example.json').read_text())['providers']


def provider(name):
    return build_provider(name, {**CONFIG[name], 'api_key': 'offline-key',
        'api_token': 'offline-token', 'account_id': 'offline-account', 'zone': 'offline-zone'})


def case():
    return EvalCase('one', 'One', 'https://example.test/', 'static', ['Expected'],
                    source_benchmark={'input_media_kind': 'html_page'})


def test_defaults():
    args = build_parser().parse_args(['run'])
    assert args.timeout == 30
    assert args.max_failure_retries == 1


@pytest.mark.parametrize('name', SELECTED)
def test_selected_methods_reject_case_assistance_before_transport(name):
    p = provider(name)
    assert p.config['uniform_native_parameters'] is True
    hinted = replace(case(), retrieval_hints={'completion': {'selector': '#answer'}})
    with pytest.raises(ValueError, match='per-case'):
        p.fetch(hinted, 30)


@pytest.mark.parametrize('name', SELECTED[1:])
def test_request_is_independent_of_grading_requirements(name):
    p = provider(name)
    first = case()
    second = replace(first, expected_any=['Different answer'], acceptance_contract={
        'schema': 'named_link', 'required_links': [{'url': 'https://answer.test/'}]})
    endpoint, args = p.native_http_arguments(first, 30)
    other_endpoint, other = p.native_http_arguments(second, 30)
    assert endpoint == other_endpoint
    assert normalized_http_parameters(first, args) == normalized_http_parameters(second, other)
    assert args['timeout'] == 30
    if name == 'exa':
        assert args['json_body']['extras']['richLinks'] == 100
    if name == 'cloudflare-markdown':
        document = replace(first, url='https://example.test/file.pdf', track='document')
        doc_endpoint, doc_args = p.native_http_arguments(document, 30)
        assert doc_endpoint.endswith('/markdown')
        assert doc_args['json_body']['url'] == document.url
        assert 'multipart' not in doc_args


@pytest.mark.parametrize('name', SELECTED[5:])
@pytest.mark.parametrize('answers', [('Expected',), ('wrong', 'Expected'), ('wrong', 'still wrong'), ('HTTP_ERROR', 'Expected')])
def test_hosted_native_retry_persists_every_physical_answer(tmp_path, name, answers):
    p, c = provider(name), case()
    texts = iter(answers)
    def respond(request, **kwargs):
        text = next(texts)
        payload = ({'body': text, 'status_code': 200, 'final_url': c.url} if name == 'browser-use-fetch'
                   else {'markdown': text} if name == 'crawl4ai-cloud'
                   else {'content': text, 'title': 'Title', 'statusCode': 200})
        raw = text.encode() if name == 'brightdata' else json.dumps(payload).encode()
        assert kwargs['timeout'] <= 30
        return Mock(status=503 if text == 'HTTP_ERROR' else 200, headers={}, read=Mock(return_value=raw),
                    geturl=Mock(return_value=request.full_url))
    with patch('harness.web_fetch.providers.http_client.urllib.request.build_opener') as opener:
        opener.return_value.open.side_effect = respond
        rows, _ = run_evaluation(output_dir=tmp_path, dataset_meta={'id':'offline', 'cases':[asdict(c)]},
            dataset_path=tmp_path/'dataset.json', cases=[c], providers=[p],
            provider_config_path=tmp_path/'providers.json', runs=1, timeout=30, delay=0)
    assert opener.return_value.open.call_count == len(answers)
    row = rows[0]
    assert (tmp_path/row['artifact_path']).read_text() == answers[-1]
    assert row['provider_metadata']['latency_scope'] == 'native_response_received'
    assert (tmp_path/row['provider_metadata']['raw_response_path']).is_file()
    assert (tmp_path/row['provider_metadata']['diagnostic_log_path']).is_file()
    verify_saved_native_answer(tmp_path, row, cases_by_id={c.id: asdict(c)})
    # Raw responses and caller logs remain separate for each paid physical call.
    assert len(list(tmp_path.rglob('*.response.bin'))) == len(answers)


def test_native_preparation_does_not_consume_response_budget():
    from harness.web_fetch.call_boundary import fetch_with_deadline, prepare_answer
    from harness.web_fetch.models import ProviderResult
    p = build_provider('fixture', {'type':'fixture', 'uniform_native_parameters':True})
    p.fetch = Mock(return_value=ProviderResult(True, 'Expected'))
    def slow_prepare(result, c):
        time.sleep(.08)
        return prepare_answer(result, c)
    with patch('harness.web_fetch.call_boundary.prepare_answer', side_effect=slow_prepare):
        result, _ = fetch_with_deadline(p, case(), .05)
    assert result.ok
    assert result.metadata['_answer_elapsed_ms'] < 50


@pytest.mark.parametrize('name', SELECTED[6:])
def test_malformed_api_response_is_retained_as_failure(tmp_path, name):
    p, c = provider(name), case()
    with patch('harness.web_fetch.providers.http_client.urllib.request.build_opener') as opener:
        opener.return_value.open.return_value = Mock(status=200, headers={},
            read=Mock(return_value=b'not valid JSON'), geturl=Mock(return_value='https://service.test/'))
        rows, _ = run_evaluation(output_dir=tmp_path, dataset_meta={'id':'offline', 'cases':[asdict(c)]},
            dataset_path=tmp_path/'dataset.json', cases=[c], providers=[p],
            provider_config_path=tmp_path/'providers.json', runs=1, timeout=30, delay=0)
    assert opener.return_value.open.call_count == 2
    row = rows[0]
    assert not row['ok'] and row['error_type'] == 'invalid_response'
    assert (tmp_path/row['artifact_path']).read_text() == ''
    assert (tmp_path/row['provider_metadata']['raw_response_path']).read_bytes() == b'not valid JSON'
    verify_saved_native_answer(tmp_path, row, cases_by_id={c.id: asdict(c)})


def test_moli_native_command_has_no_case_hint_or_reduced_timer():
    p = provider('moli-webfetch')
    first = case()
    second = replace(first, expected_any=['Another answer'], acceptance_contract={'schema':'named_link'})
    assert p.native_cli_command(first) == p.native_cli_command(second)
    command = p.native_cli_command(first)
    assert '--wait-until' in command  # Fixed product-native completion option.
    assert not any('timeout' in arg or 'selector' in arg for arg in command)


def test_configured_cohort_and_local_configuration_guards():
    from harness.web_fetch.config import resolve_provider_selection
    names = resolve_provider_selection('configured', Path('configs/providers.example.json')).split(',')
    assert tuple(names) == SELECTED
    assert resolve_provider_selection('fixture', None) == 'fixture'
    assert resolve_provider_selection(None, None) is None
    for name in names:
        assert CONFIG[name]['log_level'] == 'debug'
    p = provider('moli-webfetch')
    assert not p._configuration_errors('/offline/moli')


@pytest.mark.parametrize('names', [[], ['missing'], ['fixture', 'fixture']])
def test_configured_cohort_rejects_invalid_selection(tmp_path, names):
    from harness.web_fetch.config import resolve_provider_selection
    config = tmp_path/'providers.json'
    config.write_text(json.dumps({'providers':{'fixture':{'type':'fixture'}}, 'default_providers':names}))
    with pytest.raises(ValueError):
        resolve_provider_selection('configured', config)


def test_configured_cli_selection_reaches_runner(tmp_path):
    from harness.web_fetch.cli import command_run
    config = tmp_path/'providers.json'
    config.write_text(json.dumps({'providers':{'fixture':{'type':'fixture'}}, 'default_providers':['fixture']}))
    args = build_parser().parse_args(['run','--providers','configured','--provider-config',str(config),
                                     '--output',str(tmp_path/'run')])
    with patch('harness.web_fetch.cli.load_runtime_env'), \
         patch('harness.web_fetch.cli.run_evaluation', return_value=([], {})) as run, \
         patch('harness.web_fetch.cli.build_report', return_value=tmp_path/'report'):
        command_run(args)
    assert [p.name for p in run.call_args.kwargs['providers']] == ['fixture']
    assert run.call_args.kwargs['timeout'] == 30
    assert run.call_args.kwargs['max_failure_retries'] == 1


@pytest.mark.parametrize('name', SELECTED)
@pytest.mark.parametrize('origin_status', [200, 404, None])
def test_selected_origin_status_policy_controls_acceptance_and_retry(tmp_path, name, origin_status):
    import hashlib
    import sys
    from lexbench_webfetch.metrics import task_passed
    p, c = provider(name), case()
    exposed = name in {'moli-webfetch', 'firecrawl', 'cloudflare-markdown',
                       'browser-use-fetch', 'browserbase', 'crawl4ai-cloud'}
    if name == 'moli-webfetch':
        binary = tmp_path / 'moli-fixture'
        event = (f'DEBUG fetch request complete method=GET url={c.url} '
                 f'final_url={c.url} status={origin_status}') if origin_status is not None else ''
        binary.write_text(f'#!{sys.executable}\nimport sys\n'
            'if sys.argv[1:] == ["--version"]:\n print("moli fixture"); sys.exit(0)\n'
            f'print("Expected")\nprint({event!r}, file=sys.stderr)\n')
        binary.chmod(0o700)
        p = build_provider(name, {**p.config, 'binary': str(binary),
            'expected_reported_version': 'moli fixture',
            'expected_binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()})
    payloads = {
        'firecrawl': {'success': True, 'data': {'markdown': 'Expected', 'metadata': {'statusCode': origin_status}}},
        'cloudflare-markdown': {'success': True, 'result': 'Expected', 'meta': {'status': origin_status}},
        'tavily': {'results': [{'url': c.url, 'raw_content': 'Expected'}]},
        'exa': {'results': [{'url': c.url, 'text': 'Expected'}]},
        'browser-use-fetch': {'body': 'Expected', 'status_code': origin_status, 'final_url': c.url},
        'browserbase': {'content': 'Expected', 'statusCode': origin_status},
        'deepseek-fetch-api': {'content': 'Expected', 'title': 'Title'},
        'crawl4ai-cloud': {'markdown': 'Expected', 'origin_status': origin_status},
    }
    # The selected Browser Use and Browserbase schemas require status on success;
    # missing status is malformed rather than an invented origin error.
    malformed = origin_status is None and name in {'browser-use-fetch', 'browserbase'}
    raw = b'Expected' if name == 'brightdata' else json.dumps(payloads.get(name, {})).encode()
    def respond(request, **kwargs):
        return Mock(status=200, headers={}, read=Mock(return_value=raw),
                    geturl=Mock(return_value=request.full_url))
    with patch('harness.web_fetch.network_preflight.socket.getaddrinfo',
               return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]), \
            patch('harness.web_fetch.providers.http_client.urllib.request.build_opener') as opener:
        opener.return_value.open.side_effect = respond
        rows, _ = run_evaluation(output_dir=tmp_path / 'run',
            dataset_meta={'id': 'origin-control', 'cases': [asdict(c)]},
            dataset_path=tmp_path / 'dataset.json', cases=[c], providers=[p],
            provider_config_path=tmp_path / 'providers.json', runs=1,
            timeout=30, delay=0, max_failure_retries=1)
    row = rows[0]
    expected_pass = not ((exposed and origin_status == 404) or malformed)
    assert task_passed(row['ok'], row['metrics'], c.task_kind) == expected_pass
    metadata = row['provider_metadata']
    assert metadata['upstream_status'] == (origin_status if exposed else None)
    assert metadata['api_http_status'] == (None if name == 'moli-webfetch' else 200)
    assert metadata['upstream_http_status_available'] == (exposed and origin_status is not None)
    assert len(list((tmp_path / 'run' / 'call-logs').rglob('*.json'))) == (1 if expected_pass else 2)
    assert 'Expected' in (tmp_path / 'run' / row['artifact_path']).read_text()
