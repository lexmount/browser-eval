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
    from lexbench_webfetch.models import EvalCase
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
    from lexbench_webfetch.models import EvalCase
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
    from lexbench_webfetch.models import EvalCase
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
    from lexbench_webfetch.models import EvalCase
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
    from lexbench_webfetch.models import EvalCase
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
    from lexbench_webfetch.models import EvalCase
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
    from harness.record_io import canonical_json_sha256
    bad = deepcopy(records)
    item = bad[0]['provider_metadata']['native_route_receipt']['requests'][0]
    item['signature']['parameters']['body']['format'] = 'html'
    item['sha256'] = canonical_json_sha256(item['signature'])
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
    from lexbench_webfetch.models import EvalCase
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


@pytest.mark.parametrize('stage', ['before_transport', 'after_transport'])
def test_failed_native_calls_require_an_explicit_transport_stage(tmp_path, stage):
    from datetime import datetime, timezone
    from unittest.mock import Mock
    from copy import deepcopy
    from harness.web_fetch.native_routes import verify_native_route_contract
    provider, cases, ledger, manifest, dataset = _route_http_fixture(tmp_path)
    records = []
    for case in cases:
        if stage == 'after_transport':
            provider.native_http_request(case, Mock(), *provider.native_http_arguments(case, 45)[:1],
                                         **provider.native_http_arguments(case, 45)[1])
        records.append({'provider': provider.name, 'case_id': case.id, 'url': case.url,
            'started_at': datetime.now(timezone.utc).isoformat(), 'ok': False,
            'provider_metadata': {'native_route_receipt': ledger.receipt(provider, case, ok=False)}})
    assert verify_native_route_contract(tmp_path, manifest, dataset, records)
    for defect in ('unknown_stage', 'missing_failure_stage', 'missing_signature', 'wrong_stage'):
        bad = deepcopy(records)
        receipt = bad[0]['provider_metadata']['native_route_receipt']
        if defect == 'unknown_stage': receipt.pop('transport_state')
        if defect == 'missing_failure_stage': receipt.pop('failure_stage')
        if defect == 'missing_signature':
            receipt['requests'] = []
            receipt['transport_state'] = 'started'
        if defect == 'wrong_stage': receipt['failure_stage'] = 'unknown'
        with pytest.raises(ValueError, match='Native'):
            verify_native_route_contract(tmp_path, manifest, dataset, bad)


def test_runner_persists_pre_transport_failure_without_inventing_request(tmp_path):
    import json
    from dataclasses import asdict
    from unittest.mock import patch
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.runner import run_evaluation
    case = EvalCase('case', 'Case', 'https://example.test/', 'control', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    dataset = {'tool': 'web_fetch', 'id': 'fixture', 'cases': [asdict(case)]}
    source = tmp_path/'dataset.json'
    source.write_text(json.dumps(dataset))
    provider = build_provider('tavily', {'type': 'tavily_extract', 'api_key': 'fixture',
                                        'uniform_native_parameters': True})
    with patch.object(provider, 'fetch', return_value=ProviderResult(False, '', error_type='configuration')):
        records, _ = run_evaluation(output_dir=tmp_path/'run', dataset_meta=dataset, dataset_path=source,
            cases=[case], providers=[provider], provider_config_path=tmp_path/'providers.json',
            runs=1, timeout=45, delay=0, reference_policy='frozen_existing')
    receipt = records[0]['provider_metadata']['native_route_receipt']
    assert receipt['failure_stage'] == 'before_transport'
    assert receipt['transport_state'] == 'not_started' and receipt['requests'] == []


@pytest.mark.parametrize('method,module,payload,track,kind', [
    ('tavily_extract', 'tavily', {'results': [{'raw_content': 'answer', 'images': ['https://example.test/image.png']}]}, 'image', 'resource'),
    ('tavily_extract', 'tavily', {'results': [{'raw_content': 'answer'}], 'images': ['https://example.test/image.png']}, 'image', 'resource'),
    ('exa_contents', 'exa', {'results': [{'text': 'answer', 'extras': {'richLinks': [
        {'anchor': 'Article', 'url': 'https://example.test/article'}], 'imageLinks': ['https://example.test/image.png']}}]}, 'image', 'resource'),
    ('firecrawl', 'firecrawl', {'success': True, 'data': {'markdown': 'answer', 'images': ['https://example.test/image.png'],
        'links': ['https://example.test/article'], 'videos': ['https://example.test/video.mp4'], 'video': 'https://example.test/legacy.mp4'}}, 'video', 'resource'),
    ('firecrawl', 'firecrawl', {'success': True, 'data': {'rawBase64': 'b3JpZ2luYWwgZmlsZSBieXRlcw=='}}, 'file', 'file'),
    ('cloudflare_markdown', 'cloudflare', {'success': True, 'result': 'answer'}, 'web', 'body'),
    ('cloudflare_content', 'cloudflare', {'success': True, 'result': '<main>answer</main>'}, 'web', 'body'),
    ('cloudflare_markdown', 'cloudflare', {'success': True, 'result': [{'selector': 'img', 'results': [
        {'attributes': [{'name': 'src', 'value': '/image.png'}, {'name': 'src', 'value': '/wrong.png'}]}]}]}, 'image', 'resource'),
])
def test_http_native_answers_survive_persistence_and_reject_tampering(tmp_path, method, module, payload, track, kind):
    from pathlib import Path
    import json
    from copy import deepcopy
    from unittest.mock import patch
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    from harness.web_fetch.native_routes import NativeRouteLedger
    from harness.web_fetch.runner import persist_result_artifacts
    from harness.web_fetch.call_boundary import prepare_answer
    provider = build_provider('method', {'type': method, 'api_key': 'fixture', 'api_token': 'fixture',
        'account_id': 'fixture', 'uniform_native_parameters': True, 'include_images': True,
        'include_resource_links': True, 'rich_links_limit': 12, 'formats': ['markdown', 'images', 'links']})
    case = EvalCase('case', 'Case', 'https://example.test/', 'control', [], track=track, task_kind=kind,
                    source_benchmark={'input_media_kind': 'html_page'})
    ledger = NativeRouteLedger([case], [provider])
    ledger.write(tmp_path, dataset_sha256='fixture', runtime_source_sha256='fixture')
    with patch('harness.web_fetch.providers.'+module+'.request',
               return_value=HttpResponse(200, case.url, {}, json.dumps(payload).encode())):
        result = prepare_answer(provider.fetch(case, 45), case)
    assert result.ok
    result.metadata['native_route_receipt'] = ledger.receipt(provider, case, ok=result.ok)
    altered = deepcopy(result)
    altered.metadata['images'] = ['https://example.test/forged.png']
    with pytest.raises(ValueError, match='native|projection'):
        persist_result_artifacts(tmp_path, Path('artifacts/forged.txt'), altered, request_url=case.url)
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url=case.url)


def test_missing_native_fields_cannot_be_filled_by_declaration_defaults(tmp_path):
    import base64
    import hashlib
    from copy import deepcopy
    from pathlib import Path
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.native_routes import verify_saved_native_answer
    from harness.web_fetch.runner import persist_result_artifacts

    raw = b'{"content":"answer"}'
    result = ProviderResult(True, 'answer', metadata={
        '_raw_response_b64': base64.b64encode(raw).decode(),
        'raw_response_sha256': hashlib.sha256(raw).hexdigest(),
        'raw_response_bytes': len(raw), 'images': [],
        'native_answer_projection': {'schema_version': 2, 'content_pointer': ['content'],
            'content_format': 'text', 'answer_fields': {
                'images': {'pointer': ['images'], 'default': []}}}})
    forged = deepcopy(result)
    forged.metadata['images'] = ['https://example.test/forged.png']
    forged.metadata['native_answer_projection']['answer_fields']['images']['default'] = forged.metadata['images']
    with pytest.raises(ValueError, match='defaults must be empty'):
        persist_result_artifacts(tmp_path, Path('artifacts/forged.txt'), forged)
    assert not (tmp_path/'artifacts/forged.txt').exists()

    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result)
    record = {'ok': True, 'artifact_path': 'artifacts/answer.txt',
              'provider_metadata': result.metadata,
              'metrics': {'content_sha256': hashlib.sha256(b'answer').hexdigest()}}
    verify_saved_native_answer(tmp_path, record)
    record['provider_metadata']['images'] = forged.metadata['images']
    record['provider_metadata']['native_answer_projection'] = forged.metadata['native_answer_projection']
    with pytest.raises(ValueError, match='defaults must be empty'):
        verify_saved_native_answer(tmp_path, record)


def test_projection_cannot_read_request_echo_outside_declared_answer_fields():
    import json
    from harness.web_fetch.native_projection import require_native_answer_projection
    raw = json.dumps({'result': 'answer', 'request': {'images': ['https://example.test/forged.png']}}).encode()
    metadata = {'images': ['https://example.test/forged.png'],
        'native_route_receipt': {'answer_fields': ['result']},
        'native_answer_projection': {'schema_version': 1, 'content_pointer': ['result'], 'content_format': 'text',
            'answer_fields': {'images': {'pointer': ['request', 'images'], 'format': 'identity'}}}}
    with pytest.raises(ValueError, match='frozen answer fields'):
        require_native_answer_projection('answer', metadata, raw)


@pytest.mark.parametrize('field,forged', [('title', 'forged title'), ('final_url', 'https://forged.example.test/')])
def test_native_title_and_address_are_checked_at_live_and_saved_boundaries(tmp_path, field, forged):
    import base64
    import hashlib
    import json
    from copy import deepcopy
    from pathlib import Path
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.runner import persist_result_artifacts
    from harness.web_fetch.native_routes import verify_saved_native_answer
    raw = json.dumps({'content': 'answer', 'title': 'native title',
                      'final_url': 'https://returned.example.test/'}).encode()
    result = ProviderResult(True, 'answer', title='native title', final_url='https://returned.example.test/',
        metadata={'_raw_response_b64': base64.b64encode(raw).decode(),
            'raw_response_sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_bytes': len(raw),
            'native_answer_projection': {'schema_version': 2, 'content_pointer': ['content'],
                'content_format': 'text', 'result_fields': {name: {'pointer': [name]} for name in ('title', 'final_url')}}})
    altered = deepcopy(result)
    setattr(altered, field, forged)
    with pytest.raises(ValueError, match='title or final URL'):
        persist_result_artifacts(tmp_path, Path('artifacts/forged.txt'), altered, request_url='https://requested.example.test/')
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url='https://requested.example.test/')
    record = {'ok': True, 'artifact_path': 'artifacts/answer.txt', 'title': result.title,
        'url': 'https://requested.example.test/', 'final_url': result.final_url,
        'provider_metadata': result.metadata, 'metrics': {'content_sha256': hashlib.sha256(b'answer').hexdigest()}}
    verify_saved_native_answer(tmp_path, record)
    record[field] = forged
    with pytest.raises(ValueError, match='title or final URL'):
        verify_saved_native_answer(tmp_path, record)


def test_native_url_fallback_cannot_override_a_returned_redirect():
    import json
    from harness.web_fetch.native_projection import require_native_answer_projection
    request = 'https://requested.example.test/'
    returned = 'https://returned.example.test/'
    metadata = {'native_answer_projection': {'schema_version': 2, 'content_format': 'empty',
        'result_fields': {'final_url': {'pointer': ['url'], 'fallback': 'request_url'}}}}
    require_native_answer_projection('', metadata, b'{}', request_url=request, final_url=request)
    require_native_answer_projection('', metadata, json.dumps({'url': returned}).encode(), request_url=request, final_url=returned)
    with pytest.raises(ValueError, match='title or final URL'):
        require_native_answer_projection('', metadata, json.dumps({'url': returned}).encode(), request_url=request, final_url=request)


def test_cli_final_url_replays_only_same_call_main_document_log():
    from harness.web_fetch.native_projection import require_native_answer_projection
    request = 'https://requested.example.test/'
    returned = 'https://returned.example.test/'
    log = f'fetch request complete method=GET url={request} final_url={returned} status=200'.encode()
    metadata = {'native_answer_projection': {'schema_version': 2, 'source_format': 'bytes',
        'content_format': 'utf8', 'result_fields': {'final_url': {'format': 'fetch_log_final_url'}}}}
    require_native_answer_projection('answer', metadata, b'answer', request_url=request, final_url=returned, diagnostic=log)
    with pytest.raises(ValueError, match='title or final URL'):
        require_native_answer_projection('answer', metadata, b'answer', request_url=request, final_url=request, diagnostic=log)
    with pytest.raises(ValueError, match='diagnostic evidence'):
        require_native_answer_projection('answer', metadata, b'answer', request_url=request, final_url=returned)


@pytest.mark.parametrize('method,module,payload', [
    ('cloudflare_markdown', 'cloudflare', {'success': False, 'meta': {'title': 'Origin unavailable', 'finalUrl': 'https://returned.example.test/'}}),
    ('cloudflare_content', 'cloudflare', {'success': False, 'meta': {'finalUrl': 'https://returned.example.test/'}}),
    ('firecrawl', 'firecrawl', {'success': False, 'data': {'metadata': {'url': 'https://returned.example.test/'}}}),
])
def test_failed_native_envelope_retains_provable_result_context(tmp_path, method, module, payload):
    import json
    from pathlib import Path
    from unittest.mock import patch
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    from harness.web_fetch.runner import persist_result_artifacts
    provider = build_provider('method', {'type': method, 'api_key': 'fixture', 'api_token': 'fixture',
        'account_id': 'fixture', 'uniform_native_parameters': True})
    case = EvalCase('case', 'Case', 'https://requested.example.test/', 'control', [])
    with patch('harness.web_fetch.providers.'+module+'.request',
               return_value=HttpResponse(502, 'https://api.example.test/', {}, json.dumps(payload).encode())):
        result = provider.fetch(case, 45)
    assert not result.ok
    assert result.final_url == 'https://returned.example.test/'
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url=case.url)


def test_resource_adapter_uses_returned_address_before_resolving_relative_attributes(tmp_path):
    import json
    from pathlib import Path
    from unittest.mock import patch
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    from harness.web_fetch.runner import persist_result_artifacts
    provider = build_provider('method', {'type': 'cloudflare_markdown', 'api_token': 'fixture',
        'account_id': 'fixture', 'uniform_native_parameters': True})
    case = EvalCase('case', 'Case', 'https://requested.example.test/', 'control', [], track='image', task_kind='resource')
    payload = {'success': True, 'meta': {'finalUrl': 'https://returned.example.test/page'},
        'result': [{'results': [{'attributes': [{'name': 'src', 'value': '/asset.png'}]}]}]}
    with patch('harness.web_fetch.providers.cloudflare.request',
               return_value=HttpResponse(200, 'https://api.example.test/', {}, json.dumps(payload).encode())):
        result = provider.fetch(case, 45)
    assert result.final_url == 'https://returned.example.test/page'
    assert result.metadata['images'] == ['https://returned.example.test/asset.png']
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url=case.url)



def test_empty_same_call_diagnostic_is_retained_without_inventing_final_url(tmp_path):
    import base64
    import hashlib
    from pathlib import Path
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.runner import persist_result_artifacts
    from harness.web_fetch.native_routes import verify_saved_native_answer
    result = ProviderResult(True, 'answer', metadata={
        '_raw_stdout_b64': base64.b64encode(b'answer').decode(), 'raw_stdout_sha256': hashlib.sha256(b'answer').hexdigest(),
        '_diagnostic_log_b64': '', 'diagnostic_log_sha256': hashlib.sha256(b'').hexdigest(), 'diagnostic_log_bytes': 0,
        'native_answer_projection': {'schema_version': 2, 'source_format': 'bytes', 'content_format': 'utf8',
            'result_fields': {'final_url': {'format': 'fetch_log_final_url'}}}})
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url='https://example.test/')
    record = {'ok': True, 'url': 'https://example.test/', 'title': None, 'final_url': None,
        'artifact_path': 'artifacts/answer.txt', 'provider_metadata': result.metadata,
        'metrics': {'content_sha256': hashlib.sha256(b'answer').hexdigest()}}
    verify_saved_native_answer(tmp_path, record)
    assert (tmp_path/result.metadata['diagnostic_log_path']).read_bytes() == b''


@pytest.mark.parametrize('result_fields', [{}, {'final_url': {'pointer': ['final_url']}}])
@pytest.mark.parametrize('discarded_timeout', [False, True])
def test_current_writer_rejects_schema_one_result_context(tmp_path, result_fields, discarded_timeout):
    import base64
    import json
    from pathlib import Path
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.runner import persist_result_artifacts
    content = '' if discarded_timeout else 'answer'
    raw = json.dumps({'content': content}).encode()
    declaration = {'schema_version': 1, 'content_pointer': ['content'],
                   'content_format': 'text', 'result_fields': result_fields}
    metadata = {'native_answer_projection': declaration,
                '_raw_response_b64': base64.b64encode(raw).decode()}
    result = ProviderResult(not discarded_timeout, content,
                            error_type='timeout' if discarded_timeout else None, metadata=metadata)
    artifact = Path('artifacts/answer.txt')
    with pytest.raises(ValueError, match='result_fields require projection schema 2'):
        persist_result_artifacts(tmp_path, artifact, result, request_url='https://example.test/')
    assert not (tmp_path/artifact).exists()
    with pytest.raises(ValueError, match='result_fields require projection schema 2'):
        from harness.web_fetch.native_projection import require_native_answer_projection
        require_native_answer_projection(content, metadata, raw, request_url='https://example.test/')


def test_saved_schema_one_keeps_original_answer_scope(tmp_path):
    import base64
    import hashlib
    import json
    from pathlib import Path
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.runner import persist_result_artifacts
    from harness.web_fetch.native_routes import verify_saved_native_answer
    images = [{'src': 'https://example.test/image.png'}]
    raw = json.dumps({'content': 'answer', 'images': images}).encode()
    declaration = {'schema_version': 1, 'content_pointer': ['content'], 'content_format': 'text',
                   'answer_fields': {'images': {'pointer': ['images']}}}
    result = ProviderResult(True, 'answer', metadata={'images': images,
        'native_answer_projection': declaration, '_raw_response_b64': base64.b64encode(raw).decode(),
        'raw_response_sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_bytes': len(raw)})
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result)
    record = {'ok': True, 'artifact_path': 'artifacts/answer.txt',
              'url': 'https://requested.example.test/', 'title': 'historical unaudited title',
              'final_url': 'https://historical.example.test/', 'provider_metadata': result.metadata,
              'metrics': {'content_sha256': hashlib.sha256(b'answer').hexdigest()}}
    verify_saved_native_answer(tmp_path, record)
    result.metadata['images'] = [{'src': 'https://forged.example.test/image.png'}]
    with pytest.raises(ValueError, match='declared native field projection'):
        verify_saved_native_answer(tmp_path, record)
    result.metadata['images'] = images
    declaration['result_fields'] = {}
    with pytest.raises(ValueError, match='result_fields require projection schema 2'):
        verify_saved_native_answer(tmp_path, record)


@pytest.mark.parametrize('retries', [0, 1, 3])
def test_native_http_retries_keep_each_physical_route_receipt(tmp_path, retries):
    import json
    from copy import deepcopy
    from dataclasses import asdict
    from unittest.mock import patch
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.native_routes import verify_native_route_contract
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    from harness.web_fetch.runner import run_evaluation

    case = EvalCase('one', 'One', 'https://example.test/one', 'control', ['Expected'],
                    source_benchmark={'input_media_kind': 'html_page'})
    dataset = {'id': 'retry', 'cases': [asdict(case)]}
    source = tmp_path/'dataset.json'
    source.write_text(json.dumps(dataset))
    provider = build_provider('tavily', {'type': 'tavily_extract', 'api_key': 'fixture',
                                        'uniform_native_parameters': True})
    calls = []

    def transport(endpoint, **kwargs):
        calls.append(kwargs)
        content = 'Expected' if len(calls) == retries + 1 else 'incomplete'
        return HttpResponse(200, endpoint, {}, json.dumps({'results': [
            {'url': case.url, 'raw_content': content}]}).encode())

    output = tmp_path/'run'
    with patch('harness.web_fetch.providers.tavily.request', side_effect=transport):
        rows, _ = run_evaluation(output_dir=output, dataset_meta=dataset, dataset_path=source,
            cases=[case], providers=[provider], provider_config_path=tmp_path/'providers.json',
            runs=1, timeout=45, delay=0, reference_policy='frozen_existing',
            max_failure_retries=retries)
    assert len(calls) == retries + 1
    assert len(rows) == 1 and rows[0]['metrics']['content_pass']
    manifest = json.loads((output/'manifest.json').read_text())
    assert verify_native_route_contract(output, manifest, dataset, rows)
    if not retries:
        return
    attempts = rows[0]['provider_metadata']['retry']['attempts']
    assert [a['native_route_receipt']['physical_attempt'] for a in attempts] == list(range(1, retries + 2))
    for defect in ('missing', 'signature', 'ordinal', 'final'):
        broken = deepcopy(rows)
        physical = broken[0]['provider_metadata']['retry']['attempts'][0]
        if defect == 'missing':
            physical.pop('native_route_receipt')
        elif defect == 'signature':
            physical['native_route_receipt']['requests'][0]['sha256'] = '0'*64
        elif defect == 'ordinal':
            physical['native_route_receipt']['physical_attempt'] = 2
        else:
            broken[0]['provider_metadata']['native_route_receipt']['physical_attempt'] = 99
        with pytest.raises(ValueError, match='Native'):
            verify_native_route_contract(output, manifest, dataset, broken)


def test_native_retry_does_not_allow_two_requests_in_one_physical_call(tmp_path):
    from unittest.mock import Mock
    provider, cases, ledger, _, _ = _route_http_fixture(tmp_path)
    case = cases[0]
    ledger.begin_attempt(provider, case, 1)
    endpoint, arguments = provider.native_http_arguments(case, 45)
    transport = Mock()
    provider.native_http_request(case, transport, endpoint, **arguments)
    with pytest.raises(ValueError, match='one adapter invocation per physical attempt'):
        provider.native_http_request(case, transport, endpoint, **arguments)
    assert transport.call_count == 1


@pytest.mark.parametrize('method,module', [
    ('tavily_extract', 'tavily'), ('exa_contents', 'exa'),
    ('firecrawl', 'firecrawl'), ('cloudflare_markdown', 'cloudflare'),
    ('cloudflare_content', 'cloudflare'),
])
def test_shared_http_builder_controls_freeze_and_fetch(tmp_path, method, module):
    from unittest.mock import patch
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.native_routes import NativeRouteLedger, normalized_http_parameters
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.providers.http_client import HttpResponse
    provider = build_provider('method', {
        'type': method, 'uniform_native_parameters': True, 'api_key': 'fixture',
        'api_token': 'fixture', 'account_id': 'fixture', 'max_bytes': 12345,
    })
    case = EvalCase('case', 'Case', 'https://example.test/', 'control', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    # A shared builder change must propagate to both the pre-call declaration
    # and actual adapter transport. The endpoint/body are deliberately not
    # repeated in this test as a second provider implementation.
    builder = '_http_arguments' if method.startswith('cloudflare_') else 'native_http_arguments'
    original = getattr(provider, builder)
    def configured_arguments(*args, **kwargs):
        endpoint, arguments = original(*args, **kwargs)
        arguments['max_bytes'] = 54321
        return endpoint, arguments
    setattr(provider, builder, configured_arguments)
    ledger = NativeRouteLedger([case], [provider], timeout_seconds=17)
    ledger.write(tmp_path, dataset_sha256='fixture', runtime_source_sha256='fixture')
    with patch('harness.web_fetch.providers.'+module+'.request',
               return_value=HttpResponse(200, case.url, {}, b'{}')) as transport:
        provider.fetch(case, 17)
    assert transport.call_count == 1
    endpoint, arguments = transport.call_args
    assert arguments['max_bytes'] == 54321
    assert normalized_http_parameters(case, arguments) == next(iter(ledger.cells.values()))['request_parameters']
    assert not ledger.errors


@pytest.mark.parametrize('defect', ['endpoint', 'parameters', 'input_url', 'configuration'])
def test_direct_native_arguments_are_checked_without_a_ledger(defect):
    from unittest.mock import Mock
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.providers.base import build_provider
    provider = build_provider('tavily', {'type': 'tavily_extract', 'api_key': 'fixture',
                                        'uniform_native_parameters': True})
    case = EvalCase('case', 'Case', 'https://example.test/', 'control', [])
    assert not hasattr(provider, '_native_route_ledger')
    endpoint, arguments = provider.native_http_arguments(case, 17)
    transport = Mock()
    provider.native_http_request(case, transport, endpoint, **arguments)
    assert transport.call_count == 1
    if defect == 'endpoint':
        endpoint += '/wrong'
    elif defect == 'parameters':
        arguments['json_body']['format'] = 'html'
    elif defect == 'input_url':
        arguments['json_body']['urls'] = ['https://other.test/']
    else:
        provider.config['include_images'] = True
    with pytest.raises(ValueError, match='canonical request builder'):
        provider.native_http_request(case, transport, endpoint, **arguments)
    assert transport.call_count == 1


# Synthetic declared inputs: this is not a replay of the historical 430 tasks,
# whose input-media declarations are absent. Freeze before refactoring routes.
def _synthetic_route_baseline():
    from itertools import product
    from lexbench_webfetch.models import EvalCase, TRACKS, TASK_KINDS
    from harness.record_io import canonical_json_sha256
    from harness.web_fetch.native_routes import native_route_cell, normalized_http_parameters
    from harness.web_fetch.providers.base import build_provider
    methods = ('moli_webfetch', 'firecrawl', 'tavily_extract', 'exa_contents',
               'cloudflare_markdown', 'cloudflare_content', 'subprocess_jsonl',
               'cowagent_builtin', 'fixture')
    baseline = {}
    for method, configured in product(methods, (False, True)):
        config = {'type': method, 'uniform_native_parameters': True}
        if configured:
            config.update(api_key='synthetic-key', api_token='synthetic-token',
                          account_id='synthetic-account', browser='chromium',
                          base_url='https://service.example.test', max_bytes=123456,
                          max_age=99, max_age_hours=2, only_main_content=False,
                          include_images=True, extract_depth='basic',
                          text_verbosity='full', include_resource_links=True,
                          rich_links_limit=7, wait_until='domcontentloaded',
                          block_private_networks=True, dns_mode='system',
                          log_level='debug', formats=['markdown', 'links'],
                          command=['synthetic-worker'])
        provider = build_provider(method, config)
        # CowAgent derives host-specific runtime paths at construction. The
        # route declaration consumes configuration, so freeze explicit portable
        # configuration here rather than hashing the test host's Python path.
        provider.config = config
        rows = []
        for track, kind, media in product(sorted(TRACKS), TASK_KINDS, sorted(INPUT_MEDIA_KINDS)):
            case = EvalCase('synthetic', 'Synthetic', 'https://input.example.test/value',
                            'control', [], track=track, task_kind=kind,
                            source_benchmark={'input_media_kind': media})
            key, cell = native_route_cell(provider, case)
            row = {'key': key, 'cell': cell}
            if cell['transport'] == 'http':
                endpoint, arguments = provider.native_http_arguments(case, 45)
                row.update(endpoint=endpoint, parameters=normalized_http_parameters(case, arguments))
            rows.append(row)
        baseline[method + (':configured' if configured else ':defaults')] = {
            'cells': len(rows), 'sha256': canonical_json_sha256(rows)}
    return baseline


def test_synthetic_all_provider_route_declarations_match_frozen_baseline():
    import json
    from pathlib import Path
    expected = json.loads((Path(__file__).parent / 'fixtures/native-route-baseline.json').read_text())
    assert _synthetic_route_baseline() == expected
