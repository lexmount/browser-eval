"""Exercise saved authorization with real adapter builders and frozen ledgers."""
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from harness.web_fetch.config import expand_env
from harness.web_fetch.native_routes import NativeRouteLedger
from harness.web_fetch.providers.base import build_provider
from harness.web_fetch.report_saved_matrix import _SourceBindings, _parameter_execution
from lexbench_webfetch.models import EvalCase
from tests.test_saved_matrix_evidence import write


def saved_answer(root, record, case):
    """Retain a complete native payload and its real selection declaration."""
    import hashlib
    import json
    from harness.web_fetch.native_projection import native_answer_projection, project_native_answer
    payload = {'results': [{'url': case['url'], 'raw_content': 'answer'}]}
    raw = json.dumps(payload).encode()
    (root/'private_artifacts').mkdir(exist_ok=True)
    (root/'private_artifacts/raw.json').write_bytes(raw)
    (root/'answer.txt').write_text('answer', encoding='utf-8')
    record.update(status_code=200, final_url=case['url'], artifact_path='answer.txt',
                  metrics={'content_sha256': hashlib.sha256(b'answer').hexdigest(),
                           'content_pass': True, 'request_ok': True,
                           'scoring_source_sha256': 'a'*64})
    record['provider_metadata'].update(raw_response_path='private_artifacts/raw.json',
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        raw_response_bytes=len(raw),
        native_answer_projection=native_answer_projection('tavily_extract', EvalCase(**case), payload, 200))
    _, fields = project_native_answer(payload, record['provider_metadata']['native_answer_projection'])
    record['provider_metadata'].update(fields)


def execution(tmp_path, config, *, dns=None, name='method'):
    binding = write(tmp_path/'providers.json', {'providers': {name: config}})
    provider = build_provider(name, expand_env(config))
    case = EvalCase('case', 'Case', 'https://example.test/', 'control', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    ledger = NativeRouteLedger([case], [provider], timeout_seconds=30)
    native = ledger.write(tmp_path, dataset_sha256='dataset', runtime_source_sha256='runtime')
    started = datetime.now(timezone.utc).isoformat()
    endpoint, arguments = provider.native_http_arguments(case, 30)
    provider.native_http_request(case, lambda *a, **kw: object(), endpoint,
                                 native_route_ledger=ledger, **arguments)
    manifest = {'created_at': started, 'native_routes_required': True, 'native_route_contract': native,
                'dataset_sha256': 'dataset', 'runtime_source_sha256': 'runtime',
                'provider_config_source': str(tmp_path/'providers.json'),
                'provider_config_sha256': binding['sha256'],
                'parameters': {'timeout': 30, 'max_failure_retries': 1},
                'providers': [{'name': name, 'fixed_native_parameters': True}]}
    write(tmp_path/'manifest.json', manifest)
    record = {'provider': name, 'provider_type': provider.type, 'case_id': case.id,
              'url': case.url, 'started_at': started, 'ok': True,
              'provider_metadata': {'native_route_receipt': ledger.receipt(provider, case)}}
    if dns is not None:
        record['provider_metadata']['dns_mode'] = dns
    contract = {'provider_config_sha256': binding['sha256'], 'timeout_seconds': 30,
                'max_failure_retries': 1}
    return record, asdict(case), contract


def test_environment_options_match_collection_and_reject_drift(tmp_path, monkeypatch):
    monkeypatch.setenv('PR131_ACCOUNT', 'fixture-account')
    monkeypatch.setenv('PR131_WAIT', 'networkidle2')
    config = {'type': 'cloudflare_markdown', 'uniform_native_parameters': True,
              'api_token': '${PR131_TOKEN}', 'account_id': '${PR131_ACCOUNT}',
              'wait_until': '${PR131_WAIT}'}
    record, case, contract = execution(tmp_path, config)
    def check():
        return _parameter_execution(tmp_path, record, case, contract, _SourceBindings(tmp_path))
    assert check()['request']['body']['gotoOptions']['waitUntil'] == 'networkidle2'
    monkeypatch.setenv('PR131_ACCOUNT', 'different-account')
    with pytest.raises(ValueError, match='native options'):
        check()
    monkeypatch.setenv('PR131_ACCOUNT', 'fixture-account')
    monkeypatch.setenv('PR131_WAIT', 'domcontentloaded')
    with pytest.raises(ValueError, match='native options'):
        check()


@pytest.mark.parametrize('kind', ['cloudflare_markdown', 'cloudflare_content', 'exa_contents',
                                  'tavily_extract', 'firecrawl', 'browser_use_fetch'])
def test_historical_system_dns_uses_bound_native_contract(tmp_path, kind):
    record, case, contract = execution(tmp_path, {
        'type': kind, 'uniform_native_parameters': True, 'account_id': 'fixture',
        'api_key': 'fixture', 'api_token': 'fixture'})
    assert _parameter_execution(tmp_path, record, case, contract, _SourceBindings(tmp_path))['dns_mode'] == 'system'
    record['provider_metadata']['dns_mode'] = 'cloudflare'
    with pytest.raises(ValueError, match='DNS differs'):
        _parameter_execution(tmp_path, record, case, contract, _SourceBindings(tmp_path))


def test_missing_dns_does_not_invent_a_non_system_mode(tmp_path):
    record, case, contract = execution(tmp_path, {
        'type': 'tavily_extract', 'uniform_native_parameters': True,
        'api_key': 'fixture', 'dns_mode': 'public_doh'})
    with pytest.raises(ValueError, match='DNS differs'):
        _parameter_execution(tmp_path, record, case, contract, _SourceBindings(tmp_path))
