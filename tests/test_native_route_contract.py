"""Declared input types select native routes without URL or answer inspection."""
from types import SimpleNamespace

import pytest

from harness.web_fetch.native_routes import INPUT_MEDIA_KINDS, declared_input_media_kind
from harness.web_fetch.providers.moli import _route_for_case


@pytest.mark.parametrize('media', sorted(INPUT_MEDIA_KINDS))
def test_dataset_and_worker_use_the_same_explicit_input_type(media):
    assert declared_input_media_kind({'source_benchmark': {'input_media_kind': media}}) == media
    assert declared_input_media_kind({'input_media_kind': media}) == media


@pytest.mark.parametrize('value', [None, '', 'guess_from_url', [], {}])
def test_invalid_or_missing_input_type_is_not_guessed(value):
    with pytest.raises(ValueError, match='declared input_media_kind'):
        declared_input_media_kind({'url': 'https://example.test/actual.pdf',
                                   'input_media_kind': value})


def test_conflicting_input_declarations_are_rejected():
    with pytest.raises(ValueError, match='Conflicting'):
        declared_input_media_kind({'input_media_kind': 'html_page',
                                   'source_benchmark': {'input_media_kind': 'direct_pdf'}})


@pytest.mark.parametrize('media,expected', [
    ('direct_pdf', 'native_document_bytes'), ('direct_docx', 'native_document_bytes'),
    ('html_page', 'ordinary_markdown'), ('direct_text', 'ordinary_markdown'),
])
def test_moli_document_selection_cannot_read_url_or_reference(media, expected):
    class DeclaredDocument:
        track = 'document'
        task_kind = 'body'
        source_benchmark = {'input_media_kind': media}
        @property
        def url(self):
            raise AssertionError('route selection inspected the URL')
        @property
        def expected_resources(self):
            raise AssertionError('route selection inspected expected answers')
    assert _route_for_case(DeclaredDocument(), native_default=True) == expected


def test_fixed_native_cohort_rejects_undeclared_media_before_preflight(tmp_path):
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.runner import run_evaluation
    provider = build_provider('fixture', {'type': 'fixture', 'uniform_native_parameters': True})
    case = EvalCase('case', 'Case', 'https://example.test/a.pdf', 'control', [])
    with pytest.raises(ValueError, match='declared input_media_kind'):
        run_evaluation(output_dir=tmp_path/'run', dataset_meta={}, dataset_path=tmp_path/'absent.json',
                       cases=[case], providers=[provider], provider_config_path=tmp_path/'absent-providers.json',
                       runs=1, timeout=45, delay=0)
    assert not (tmp_path/'run').exists()


def test_plain_provider_use_does_not_infer_optional_media():
    assert declared_input_media_kind(SimpleNamespace(source_benchmark={}), required=False) is None


def _route_http_fixture(tmp_path):
    from dataclasses import asdict
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.native_routes import NativeRouteLedger
    from harness.web_fetch.providers.base import build_provider
    provider = build_provider('tavily', {'type': 'tavily_extract', 'api_key': 'fixture',
                                        'uniform_native_parameters': True})
    cases = [EvalCase(name, name, 'https://example.test/'+name, 'control', [],
                      source_benchmark={'input_media_kind': 'html_page'})
             for name in ('one', 'two')]
    ledger = NativeRouteLedger(cases, [provider])
    binding = ledger.write(tmp_path, dataset_sha256='dataset', runtime_source_sha256='runtime')
    manifest = {'native_routes_required': True, 'native_route_contract': binding,
                'dataset_sha256': 'dataset', 'runtime_source_sha256': 'runtime',
                'providers': [{'name': provider.name, 'fixed_native_parameters': True}]}
    return provider, cases, ledger, manifest, {'cases': [asdict(c) for c in cases]}


def test_actual_native_http_requests_are_locked_before_transport(tmp_path):
    import json
    from datetime import datetime, timezone
    from unittest.mock import patch
    from harness.web_fetch.native_routes import verify_native_route_contract
    from harness.web_fetch.providers.http_client import HttpResponse
    provider, cases, ledger, manifest, dataset = _route_http_fixture(tmp_path)
    records = []
    seen = []
    def transport(endpoint, **kwargs):
        assert (tmp_path/'native-route-contract.json').exists()
        seen.append(kwargs['json_body'])
        return HttpResponse(200, endpoint, {}, json.dumps({'results': [
            {'url': kwargs['json_body']['urls'][0], 'raw_content': 'answer'}]}).encode())
    with patch('harness.web_fetch.providers.tavily.request', side_effect=transport):
        for case in cases:
            started = datetime.now(timezone.utc).isoformat()
            result = provider.fetch(case, 45)
            records.append({'provider': provider.name, 'case_id': case.id,
                            'url': case.url, 'started_at': started, 'ok': result.ok,
                            'provider_metadata': {'native_route_receipt': ledger.receipt(provider, case)}})
    assert len(seen) == 2 and seen[0]['urls'] != seen[1]['urls']
    assert len(ledger.observed) == 1
    assert all('fixture' not in str(row['provider_metadata']) for row in records)
    assert verify_native_route_contract(tmp_path, manifest, dataset, records)


@pytest.mark.parametrize('defect', ['endpoint', 'parameters', 'input_url', 'configuration'])
def test_native_request_drift_is_rejected_before_second_transport(tmp_path, defect):
    from unittest.mock import Mock
    provider, cases, ledger, _, _ = _route_http_fixture(tmp_path)
    callback = Mock(return_value=object())
    endpoint = 'https://api.tavily.com/extract'
    kwargs = {'method': 'POST', 'json_body': {'urls': [cases[0].url], 'format': 'markdown', 'extract_depth': 'advanced', 'include_images': False},
              'headers': {'Authorization': 'Bearer fixture'}, 'timeout': 45, 'max_bytes': 20000000}
    provider.native_http_request(cases[0], callback, endpoint, **kwargs)
    kwargs['json_body'] = {'urls': [cases[1].url], 'format': 'markdown', 'extract_depth': 'advanced', 'include_images': False}
    if defect == 'endpoint': endpoint = 'https://api.tavily.com/other'
    if defect == 'parameters': kwargs['json_body']['format'] = 'html'
    if defect == 'input_url': kwargs['json_body']['urls'] = ['https://wrong.test/']
    if defect == 'configuration': provider.config['include_images'] = True
    with pytest.raises(ValueError, match='Native'):
        provider.native_http_request(cases[1], callback, endpoint, **kwargs)
    assert callback.call_count == 1
    assert ledger.errors


@pytest.mark.parametrize('defect', ['missing_receipt', 'signature', 'media', 'freeze', 'scope'])
def test_release_check_rejects_changed_native_route_evidence(tmp_path, defect):
    from copy import deepcopy
    from datetime import datetime, timezone
    from unittest.mock import Mock
    from harness.web_fetch.native_routes import verify_native_route_contract
    provider, cases, ledger, manifest, dataset = _route_http_fixture(tmp_path)
    records = []
    for case in cases:
        provider.native_http_request(case, Mock(), 'https://api.tavily.com/extract',
                                     method='POST', json_body={'urls': [case.url], 'format': 'markdown',
                                         'extract_depth': 'advanced', 'include_images': False},
                                     headers={'Authorization': 'Bearer fixture'}, timeout=45, max_bytes=20000000)
        records.append({'provider': provider.name, 'case_id': case.id, 'url': case.url,
                        'started_at': datetime.now(timezone.utc).isoformat(), 'ok': True,
                        'provider_metadata': {'native_route_receipt': ledger.receipt(provider, case)}})
    records = deepcopy(records)
    if defect == 'missing_receipt': records[0]['provider_metadata'] = {}
    if defect == 'signature': records[0]['provider_metadata']['native_route_receipt']['requests'][0]['sha256'] = '0'*64
    if defect == 'media': dataset['cases'][0]['source_benchmark']['input_media_kind'] = 'direct_pdf'
    if defect == 'freeze': records[0]['started_at'] = '2000-01-01T00:00:00+00:00'
    if defect == 'scope': records.pop()
    with pytest.raises(ValueError, match='Native'):
        verify_native_route_contract(tmp_path, manifest, dataset, records)


def test_runner_freezes_route_matrix_before_mocked_provider_calls(tmp_path):
    import json
    from dataclasses import asdict
    from unittest.mock import patch
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    from harness.web_fetch.runner import run_evaluation
    cases = [EvalCase(label, label, 'https://example.test/'+label, 'control', [],
                      source_benchmark={'input_media_kind': 'html_page'}) for label in ('one', 'two')]
    dataset = {'tool': 'web_fetch', 'id': 'fixture', 'cases': [asdict(c) for c in cases]}
    source = tmp_path/'dataset.json'
    source.write_text(json.dumps(dataset))
    provider = build_provider('tavily', {'type': 'tavily_extract', 'api_key': 'fixture',
                                        'uniform_native_parameters': True})
    output = tmp_path/'run'
    def call(endpoint, **kwargs):
        frozen = json.loads((output/'native-route-contract.json').read_text())
        assert frozen['pre_call_frozen'] and len(frozen['cases']) == 2
        target = kwargs['json_body']['urls'][0]
        return HttpResponse(200, endpoint, {}, json.dumps({'results': [
            {'url': target, 'raw_content': 'complete answer'}]}).encode())
    with patch('harness.web_fetch.providers.tavily.request', side_effect=call) as transport:
        records, _ = run_evaluation(output_dir=output, dataset_meta=dataset, dataset_path=source,
                                   cases=cases, providers=[provider], provider_config_path=tmp_path/'providers.json',
                                   runs=1, timeout=45, delay=0, reference_policy='frozen_existing')
    assert transport.call_count == 2
    assert len(records) == 2
    manifest = json.loads((output/'manifest.json').read_text())
    assert manifest['native_routes_required'] and not manifest['native_route_errors']
    assert all(r['provider_metadata']['native_route_receipt']['requests'] for r in records)


def test_jsonl_worker_receives_declared_media_and_records_actual_packet(tmp_path):
    import sys
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.native_routes import NativeRouteLedger
    from harness.web_fetch.providers.base import build_provider
    script = ('import sys,json\nfor line in sys.stdin:\n'
              ' r=json.loads(line);print(json.dumps({"ok":True,"content":r["input_media_kind"]}),flush=True)\n')
    provider = build_provider('worker', {'type': 'subprocess_jsonl', 'command': [sys.executable, '-u', '-c', script],
                                        'repo_path': str(tmp_path), 'uniform_native_parameters': True})
    case = EvalCase('case', 'Case', 'https://example.test/no-extension', 'control', [], track='document',
                    source_benchmark={'input_media_kind': 'direct_pdf'})
    ledger = NativeRouteLedger([case], [provider], timeout_seconds=5)
    ledger.write(tmp_path, dataset_sha256='fixture', runtime_source_sha256='fixture')
    try:
        result = provider.fetch(case, 5)
        assert result.ok and result.content == 'direct_pdf'
        request = ledger.receipt(provider, case)['requests'][0]['signature']['parameters']['request']
        assert request['input_media_kind'] == 'direct_pdf'
        assert request['url'] == '<input-url>'
        assert str(tmp_path) not in str(request)
    finally:
        provider.cancel()


@pytest.mark.parametrize('method,module', [
    ('tavily_extract', 'tavily'), ('exa_contents', 'exa'),
    ('firecrawl', 'firecrawl'), ('cloudflare_markdown', 'cloudflare'),
    ('cloudflare_content', 'cloudflare'),
])
@pytest.mark.parametrize('track,kind,media', [
    ('web', 'body', 'html_page'), ('document', 'body', 'direct_pdf'),
    ('image', 'resource', 'html_page'), ('video', 'resource', 'html_page'),
    ('file', 'file', 'direct_text'),
])
def test_each_http_adapter_matches_its_pre_call_contract(tmp_path, method, module, track, kind, media):
    from unittest.mock import patch
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.native_routes import NativeRouteLedger
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    config = {'type': method, 'uniform_native_parameters': True,
              'api_key': 'fixture', 'api_token': 'fixture', 'account_id': 'fixture',
              'include_resource_links': True, 'rich_links_limit': 12, 'include_images': True,
              'formats': ['markdown', 'links', 'images'], 'only_main_content': False,
              'browser': 'chrome'}
    provider = build_provider('method', config)
    case = EvalCase('case', 'Case', 'https://example.test/no-extension', 'control', [],
                    track=track, task_kind=kind, source_benchmark={'input_media_kind': media})
    ledger = NativeRouteLedger([case], [provider], timeout_seconds=17)
    ledger.write(tmp_path, dataset_sha256='fixture', runtime_source_sha256='fixture')
    # Deliberately empty native output: this test proves transport selection,
    # not retrieval success, and cannot accidentally make a live request.
    with patch('harness.web_fetch.providers.'+module+'.request',
               return_value=HttpResponse(200, case.url, {}, b'{}')) as transport:
        provider.fetch(case, 17)
    assert transport.call_count == 1
    receipt = ledger.receipt(provider, case)
    assert not ledger.errors and len(receipt['requests']) == 1
    cell = next(iter(ledger.cells.values()))
    assert receipt['requests'][0]['signature']['parameters'] == cell['request_parameters']


def test_custom_firecrawl_without_auth_still_matches_actual_request(tmp_path):
    from unittest.mock import patch
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.native_routes import NativeRouteLedger
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    provider = build_provider('firecrawl', {'type': 'firecrawl', 'base_url': 'https://fixture.test',
                                           'uniform_native_parameters': True})
    case = EvalCase('case', 'Case', 'https://example.test/', 'control', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    ledger = NativeRouteLedger([case], [provider])
    ledger.write(tmp_path, dataset_sha256='fixture', runtime_source_sha256='fixture')
    with patch('harness.web_fetch.providers.firecrawl.request',
               return_value=HttpResponse(200, case.url, {}, b'{}')) as transport:
        provider.fetch(case, 45)
    assert transport.call_count == 1 and not ledger.errors


def test_contract_survives_aggregation_and_complete_declared_subset(tmp_path):
    import json
    from copy import deepcopy
    from datetime import datetime, timezone
    from unittest.mock import Mock
    from harness.web_fetch.native_routes import (
        materialize_native_route_contracts, verify_native_execution_manifests,
    )
    source, output = tmp_path/'source', tmp_path/'aggregate'
    source.mkdir()
    output.mkdir()
    provider, cases, ledger, manifest, dataset = _route_http_fixture(source)
    records = []
    for case in cases:
        provider.native_http_request(case, Mock(), 'https://api.tavily.com/extract',
            method='POST', json_body={'urls': [case.url], 'format': 'markdown',
                'extract_depth': 'advanced', 'include_images': False},
            headers={'Authorization': 'Bearer fixture'}, timeout=45, max_bytes=20000000)
        records.append({'attempt_id': case.id, 'provider': provider.name, 'case_id': case.id,
            'url': case.url, 'started_at': datetime.now(timezone.utc).isoformat(), 'ok': True,
            'provider_metadata': {'native_route_receipt': ledger.receipt(provider, case)}})
    manifest['attempt_ids'] = [r['attempt_id'] for r in records]
    original = (source/'native-route-contract.json').read_bytes()
    materialize_native_route_contracts(output, source, [manifest])
    assert (output/manifest['native_route_contract']['path']).read_bytes() == original
    assert verify_native_execution_manifests(output, [manifest], dataset, records)
    subset = deepcopy(manifest)
    subset['attempt_ids'] = [records[0]['attempt_id']]
    with pytest.raises(ValueError, match='incomplete result scope'):
        verify_native_execution_manifests(output, [subset], dataset, records)
    subset['projection'] = {'kind': 'complete-provider-case-subset-from-retained-run'}
    assert verify_native_execution_manifests(output, [subset], dataset, records)
    # Even a self-consistent forged request hash cannot change frozen parameters.
    from harness.web_fetch.native_routes import _digest
    bad = deepcopy(records)
    item = bad[0]['provider_metadata']['native_route_receipt']['requests'][0]
    item['signature']['parameters']['body']['format'] = 'html'
    item['sha256'] = _digest(item['signature'])
    with pytest.raises(ValueError, match='frozen interface'):
        verify_native_execution_manifests(output, [manifest], dataset, bad)
    contract = output/manifest['native_route_contract']['path']
    doc = json.loads(contract.read_text())
    doc['pre_call_frozen'] = False
    contract.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='hash changed'):
        verify_native_execution_manifests(output, [manifest], dataset, records)
    with pytest.raises(ValueError, match='bytes changed'):
        materialize_native_route_contracts(tmp_path/'another', output, [deepcopy(manifest)])


@pytest.mark.parametrize('media,kind', [('html_page', 'body'), ('direct_pdf', 'body'),
                                        ('html_page', 'resource'), ('direct_text', 'file')])
def test_moli_actual_cli_obeys_declared_route_without_network(tmp_path, media, kind):
    import hashlib
    import sys
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.native_routes import NativeRouteLedger
    from harness.web_fetch.providers.base import build_provider
    binary = tmp_path/'fake-moli'
    binary.write_text('#!'+sys.executable+'\nimport sys\n'
                      'print("moli 1.1.4" if sys.argv[1:]==["--version"] else "answer")\n')
    binary.chmod(0o700)
    provider = build_provider('moli', {'type': 'moli_webfetch', 'binary': str(binary),
        'expected_reported_version': 'moli 1.1.4',
        'expected_binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'uniform_native_parameters': True, 'block_private_networks': False})
    assert provider.validate() == []
    case = EvalCase('case', 'Case', 'https://example.test/no-extension', 'control', [],
                    track='document' if kind=='body' else 'image' if kind=='resource' else 'file',
                    task_kind=kind, source_benchmark={'input_media_kind': media})
    ledger = NativeRouteLedger([case], [provider], timeout_seconds=5)
    ledger.write(tmp_path, dataset_sha256='fixture', runtime_source_sha256='fixture')
    provider.fetch(case, 5)
    assert not ledger.errors and len(ledger.receipt(provider, case)['requests']) == 1


def test_route_verifier_preserves_optional_dataset_defaults(tmp_path):
    from datetime import datetime, timezone
    from unittest.mock import Mock
    from harness.web_fetch.native_routes import verify_native_route_contract
    provider, cases, ledger, manifest, dataset = _route_http_fixture(tmp_path)
    records = []
    for case in cases:
        provider.native_http_request(case, Mock(), 'https://api.tavily.com/extract',
            method='POST', json_body={'urls': [case.url], 'format': 'markdown',
                'extract_depth': 'advanced', 'include_images': False},
            headers={'Authorization': 'Bearer fixture'}, timeout=45, max_bytes=20000000)
        records.append({'provider': provider.name, 'case_id': case.id, 'url': case.url,
            'started_at': datetime.now(timezone.utc).isoformat(), 'ok': True,
            'provider_metadata': {'native_route_receipt': ledger.receipt(provider, case)}})
    for case in dataset['cases']:
        del case['track']
        del case['task_kind']
    assert verify_native_route_contract(tmp_path, manifest, dataset, records)
