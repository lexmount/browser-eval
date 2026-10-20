"""A pre-call answer policy is stricter than a post-response field allowlist."""
import json
import hashlib
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_fetch.native_projection import native_projection_policy
from harness.web_fetch.native_routes import NativeRouteLedger, verify_saved_native_answer
from harness.web_fetch.providers.base import build_provider
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch.runner import persist_result_artifacts
from lexbench_webfetch.models import EvalCase


def _saved_tavily(tmp_path, *, success=True):
    case = EvalCase('one', 'One', 'https://example.test/one', 'control', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [asdict(case)]}))
    provider = build_provider('tavily', {'type': 'tavily_extract', 'api_key': 'fixture',
                                        'uniform_native_parameters': True})
    ledger = NativeRouteLedger([case], [provider])
    ledger.write(tmp_path, dataset_sha256='dataset', runtime_source_sha256='runtime')
    image = ['https://example.test/image.png']
    payload = {'results': [{'url': case.url, 'raw_content': 'answer', 'images': image}], 'images': image}

    def transport(endpoint, **kwargs):
        contract = json.loads((tmp_path/'native-route-contract.json').read_bytes())
        assert contract['cells'][0]['answer_selection_policy'] == native_projection_policy(provider.type)
        return HttpResponse(200 if success else 500, endpoint, {}, json.dumps(payload).encode())

    with patch('harness.web_fetch.providers.tavily.request', side_effect=transport):
        result = provider.fetch(case, 45)
    result.metadata['native_route_receipt'] = ledger.receipt(provider, case, ok=result.ok)
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url=case.url)
    return {'ok': result.ok, 'provider_type': provider.type, 'case_id': case.id, 'url': case.url,
            'status_code': result.status_code, 'title': result.title, 'final_url': result.final_url,
            'artifact_path': 'artifacts/answer.txt', 'provider_metadata': result.metadata,
            'metrics': {'content_sha256': hashlib.sha256(result.content.encode()).hexdigest()}}


@pytest.mark.parametrize('success', [True, False])
def test_pre_call_policy_replays_success_and_failure(tmp_path, success):
    record = _saved_tavily(tmp_path, success=success)
    verify_saved_native_answer(tmp_path, record)


@pytest.mark.parametrize('defect', ['allowed_alternative', 'policy_hash', 'request_identity', 'deleted_declaration'])
def test_saved_projection_cannot_choose_another_allowed_field(tmp_path, defect):
    record = _saved_tavily(tmp_path)
    if defect == 'allowed_alternative':
        # Both fields carry identical bytes and are on the frozen allowlist;
        # provenance alone used to accept changing the priority after the call.
        record['provider_metadata']['native_answer_projection']['answer_fields']['images']['pointer'] = ['images']
    elif defect == 'policy_hash':
        record['provider_metadata']['native_route_receipt']['answer_selection_policy']['source_sha256'] = '0'*64
    elif defect == 'request_identity':
        record['url'] = 'https://example.test/another-request'
    else:
        record['provider_metadata'].pop('native_answer_projection')
        record['provider_metadata'].pop('native_answer_required', None)
    with pytest.raises(ValueError, match='selection|request identity'):
        verify_saved_native_answer(tmp_path, record)


def test_historical_projection_remains_reconstruction_without_pre_call_claim(tmp_path):
    record = _saved_tavily(tmp_path)
    del record['provider_metadata']['native_route_receipt']['answer_selection_policy']
    record['provider_metadata']['native_answer_projection']['answer_fields']['images']['pointer'] = ['images']
    verify_saved_native_answer(tmp_path, record)


@pytest.mark.parametrize('failure', ['before_transport', 'network', 'timeout'])
def test_empty_unanswered_failures_do_not_invent_a_projection(tmp_path, failure):
    record = _saved_tavily(tmp_path, success=False)
    metadata = record['provider_metadata']
    metadata.pop('native_answer_projection')
    metadata.pop('native_answer_required', None)
    record.update(ok=False, error_type='timeout' if failure == 'timeout' else 'network', status_code=None)
    (tmp_path/record['artifact_path']).write_bytes(b'')
    record['metrics']['content_sha256'] = hashlib.sha256(b'').hexdigest()
    if failure != 'timeout':
        for key in ('raw_response_path', 'raw_response_sha256', 'raw_response_bytes'):
            metadata.pop(key, None)
    if failure == 'before_transport':
        metadata['native_route_receipt']['requests'] = []
        metadata['native_route_receipt']['transport_state'] = 'not_started'
        metadata['native_route_receipt']['failure_stage'] = 'before_transport'
    verify_saved_native_answer(tmp_path, record)


def test_one_native_case_read_per_materialization_and_fresh_next_operation(tmp_path):
    from harness.web_fetch.artifacts import materialize_bound_artifacts
    from harness.web_fetch.native_routes import native_cases_by_id
    record = _saved_tavily(tmp_path)
    with patch('harness.web_fetch.native_routes.native_cases_by_id', wraps=native_cases_by_id) as read:
        materialize_bound_artifacts(tmp_path/'copy', tmp_path, [record, deepcopy(record)])
        assert read.call_count == 1
    dataset = json.loads((tmp_path/'dataset.json').read_bytes())
    dataset['cases'][0]['url'] = 'https://example.test/changed'
    (tmp_path/'dataset.json').write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match='request identity'):
        materialize_bound_artifacts(tmp_path/'next-copy', tmp_path, [record])


def test_moli_missing_binary_hash_rejected_before_route_transport(tmp_path):
    provider = build_provider('moli', {'type': 'moli_webfetch', 'expected_reported_version': 'fixture'})
    with patch.object(provider, '_resolve_binary', return_value='/fixture/moli'), \
         patch('harness.web_fetch.providers.moli.subprocess.check_output') as probe:
        assert 'expected_binary_sha256' in provider.validate()
        probe.assert_not_called()


@pytest.mark.parametrize('kind,payload,status,track,task_kind', [
    ('exa_contents', {'results': [{'text': 'answer', 'url': 'https://example.test/one'}]}, 200, 'web', 'body'),
    ('exa_contents', {'results': [{'text': 'answer', 'url': 'https://example.test/one'}]}, 500, 'web', 'body'),
    ('firecrawl', {'success': True, 'data': {'markdown': 'answer'}}, 200, 'web', 'body'),
    ('firecrawl', {'success': False, 'data': {'metadata': {'url': 'https://example.test/one'}}}, 500, 'web', 'body'),
    ('cloudflare_markdown', {'success': True, 'result': 'answer'}, 200, 'web', 'body'),
    ('cloudflare_markdown', {'success': False, 'meta': {'finalUrl': 'https://example.test/one'}}, 500, 'web', 'body'),
    ('cloudflare_content', {'success': True, 'result': '<h1>answer</h1>'}, 200, 'web', 'body'),
    ('cloudflare_markdown', {'success': True, 'result': []}, 200, 'image', 'resource'),
])
def test_http_adapter_and_saved_verifier_share_frozen_selection(tmp_path, kind, payload, status, track, task_kind):
    case = EvalCase('one', 'One', 'https://example.test/one', 'control', [], track=track,
                    task_kind=task_kind, source_benchmark={'input_media_kind': 'html_page'})
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [asdict(case)]}))
    provider = build_provider('method', {'type': kind, 'api_key': 'fixture', 'api_token': 'fixture',
                                         'account_id': 'fixture', 'uniform_native_parameters': True})
    ledger = NativeRouteLedger([case], [provider])
    ledger.write(tmp_path, dataset_sha256='dataset', runtime_source_sha256='runtime')
    owner = {'exa_contents': 'exa', 'firecrawl': 'firecrawl', 'cloudflare_markdown': 'cloudflare',
             'cloudflare_content': 'cloudflare'}[kind]
    with patch('harness.web_fetch.providers.'+owner+'.request',
               return_value=HttpResponse(status, 'https://api.example.test/', {}, json.dumps(payload).encode())):
        result = provider.fetch(case, 45)
    result.metadata['native_route_receipt'] = ledger.receipt(provider, case, ok=result.ok)
    persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url=case.url)
    record = {'ok': result.ok, 'provider_type': kind, 'case_id': case.id, 'url': case.url,
              'status_code': result.status_code, 'title': result.title, 'final_url': result.final_url,
              'artifact_path': 'artifacts/answer.txt', 'provider_metadata': result.metadata,
              'metrics': {'content_sha256': hashlib.sha256(result.content.encode()).hexdigest()}}
    verify_saved_native_answer(tmp_path, record)


@pytest.mark.parametrize('ok', [True, False])
def test_worker_policy_replays_optional_fields_for_both_outcomes(tmp_path, ok):
    import sys
    case = EvalCase('one', 'One', 'https://example.test/one', 'control', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [asdict(case)]}))
    payload = {'ok': ok, 'content': 'answer', 'metadata': {'images': ['https://example.test/p.png']}}
    source = 'import sys,json\nfor line in sys.stdin: print('+repr(json.dumps(payload))+',flush=True)'
    provider = build_provider('worker', {'type': 'subprocess_jsonl', 'command': [sys.executable, '-u', '-c', source],
                                        'repo_path': str(tmp_path), 'uniform_native_parameters': True})
    ledger = NativeRouteLedger([case], [provider], timeout_seconds=5)
    ledger.write(tmp_path, dataset_sha256='dataset', runtime_source_sha256='runtime')
    try:
        result = provider.fetch(case, 5)
        result.metadata['native_route_receipt'] = ledger.receipt(provider, case, ok=result.ok)
        persist_result_artifacts(tmp_path, Path('artifacts/answer.txt'), result, request_url=case.url)
        record = {'ok': result.ok, 'provider_type': 'subprocess_jsonl', 'case_id': case.id, 'url': case.url,
                  'status_code': result.status_code, 'title': result.title, 'final_url': result.final_url,
                  'artifact_path': 'artifacts/answer.txt', 'provider_metadata': result.metadata,
                  'metrics': {'content_sha256': hashlib.sha256(result.content.encode()).hexdigest()}}
        verify_saved_native_answer(tmp_path, record)
    finally:
        provider.cancel()


def test_frozen_policy_source_is_materialized_and_hash_checked(tmp_path):
    from harness.web_fetch.native_routes import materialize_native_route_contracts
    record = _saved_tavily(tmp_path)
    contract = tmp_path/'native-route-contract.json'
    manifest = {'native_route_contract': {'path': contract.name, 'sha256': hashlib.sha256(contract.read_bytes()).hexdigest()}}
    policy = record['provider_metadata']['native_route_receipt']['answer_selection_policy']
    target = tmp_path/'copy'
    materialize_native_route_contracts(target, tmp_path, [manifest])
    assert (target/policy['source_path']).read_bytes() == (tmp_path/policy['source_path']).read_bytes()
    (tmp_path/policy['source_path']).write_text('changed')
    with pytest.raises(ValueError, match='policy source changed'):
        materialize_native_route_contracts(tmp_path/'other', tmp_path,
            [{'native_route_contract': {'path': contract.name, 'sha256': hashlib.sha256(contract.read_bytes()).hexdigest()}}])
