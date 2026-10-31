"""Regression coverage for native delivery on every hosted task type."""
import base64
import json
from unittest.mock import patch

import pytest
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch.native_projection import native_answer_projection, native_projection_policy_version
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.artifacts import verify_record_artifacts
from harness.web_fetch.native_routes import verify_saved_native_answer
from lexbench_webfetch.metrics import task_passed


def provider(kind):
    return build_provider(kind, {'type': kind, 'api_key': 'synthetic-key', 'zone': 'synthetic-zone',
                                 'uniform_native_parameters': True, 'render': True})


def file_case():
    return EvalCase('native-file', 'Native file', 'https://example.test/file.png', 'image', [],
                    track='image', task_kind='file', source_benchmark={'input_media_kind': 'direct_image'})


@pytest.mark.parametrize('kind', ['brightdata_unlocker', 'deepseek_fetch', 'crawl4ai_cloud'])
def test_remaining_file_tasks_call_native_interface_and_preserve_delivery(tmp_path, kind):
    case = file_case()
    data = b'\x89PNG\r\n\x1a\nNative image bytes'
    raw = data if kind == 'brightdata_unlocker' else json.dumps(
        {'title': 'Native answer', 'content': 'Native text, not an image file'} if kind == 'deepseek_fetch'
        else {'markdown': 'Native text, not an image file', 'origin_status': 200}).encode()
    dataset = {'id': 'native-file', 'cases': [vars(case)]}
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(dataset))
    response = HttpResponse(200, 'https://service.test/', {'content-type': 'image/png'}, raw)
    with patch('harness.web_fetch.providers.hosted_fetch.request', return_value=response) as call:
        records, _ = run_evaluation(output_dir=tmp_path, dataset_meta=dataset, dataset_path=source,
            cases=[case], providers=[provider(kind)], provider_config_path=source, runs=1,
            timeout=30, delay=0, max_failure_retries=0)
    call.assert_called_once()
    record = records[0]
    assert record['ok'] is True
    metadata = record['provider_metadata']
    assert (tmp_path / metadata['raw_response_path']).read_bytes() == raw
    assert metadata['native_file_delivery'] is (kind == 'brightdata_unlocker')
    if kind == 'brightdata_unlocker':
        sent = call.call_args.kwargs['json_body']
        assert sent == {'url': case.url, 'zone': 'synthetic-zone', 'format': 'raw', 'render': 'true'}
        assert metadata['file_bytes_delivered'] == len(data)
    else:
        assert metadata['file_bytes_delivered'] == 0
        assert not task_passed(record['ok'], record['metrics'], 'file')
        assert 'Native text' in (tmp_path / record['artifact_path']).read_text()
    verify_record_artifacts(tmp_path, record)
    verify_saved_native_answer(tmp_path, record)


@pytest.mark.parametrize('kind', ['deepseek_fetch', 'crawl4ai_cloud'])
@pytest.mark.parametrize('payload, expected', [
    ({'opaque_result': {'bytes': 'unknown'}}, 'invalid_response'),
    ({'error': 'unsupported content type'}, 'api_error'),
    ({'content': 42, 'markdown': 42, 'title': ''}, 'invalid_response'),
])
def test_unknown_contract_is_separate_from_explicit_service_error(kind, payload, expected):
    raw = json.dumps(payload).encode()
    with patch('harness.web_fetch.providers.hosted_fetch.request',
               return_value=HttpResponse(200, 'https://service.test/', {}, raw)) as call:
        result = provider(kind).fetch(file_case(), 30)
    call.assert_called_once()
    assert result.error_type == expected
    assert base64.b64decode(result.metadata['_raw_response_b64']) == raw
    assert 'response_contract_review' not in result.metadata
    assert result.files == []


@pytest.mark.parametrize('version,digest', [
    (1, '7a8e133780f75c44c4cc0127d1d69a784f4b00c4cd693a1e35571f22ad95b7bf'),
    (2, '7930fb31d7cd5d98e40d76e70b59b51db6e1c62a7bb7cfdd25b4b79df9c213b7'),
])
def test_historical_policies_cannot_acquire_new_brightdata_files(version, digest):
    policy = {'kind': 'native-answer-selection', 'version': version, 'provider_type': 'brightdata_unlocker',
              'source_sha256': digest, 'source_path': 'native-answer-policies/'+digest+'.py'}
    assert native_projection_policy_version(policy, 'brightdata_unlocker') == version
    with pytest.raises(ValueError):
        native_projection_policy_version({**policy, 'version': 3}, 'brightdata_unlocker')
    old = native_answer_projection('brightdata_unlocker', file_case(), {}, 200, policy_version=version)
    assert old['content_format'] == 'utf8' and 'files' not in old
    new = native_answer_projection('brightdata_unlocker', file_case(), {}, 200)
    assert new['content_format'] == 'empty' and new['files']['format'] == 'raw_bytes'


@pytest.mark.parametrize('status', [403, 502])
def test_file_api_error_preserves_raw_response_without_fabricating_file(tmp_path, status):
    case = file_case()
    raw = b'Upstream request failed'
    dataset = {'id': 'native-file-error', 'cases': [vars(case)]}
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(dataset))
    response = HttpResponse(status, 'https://service.test/', {'content-type': 'text/plain'}, raw)
    with patch('harness.web_fetch.providers.hosted_fetch.request', return_value=response) as call:
        records, _ = run_evaluation(output_dir=tmp_path, dataset_meta=dataset, dataset_path=source,
            cases=[case], providers=[provider('brightdata_unlocker')], provider_config_path=source,
            runs=1, timeout=30, delay=0, max_failure_retries=0)
    call.assert_called_once()
    record = records[0]
    assert record['ok'] is False
    assert record['provider_metadata']['api_http_status'] == status
    assert record['provider_metadata']['file_bytes_delivered'] == 0
    assert (tmp_path / record['provider_metadata']['raw_response_path']).read_bytes() == raw
    assert (tmp_path / record['artifact_path']).read_text() == ''
    assert not task_passed(record['ok'], record['metrics'], 'file')
    verify_record_artifacts(tmp_path, record)
    verify_saved_native_answer(tmp_path, record)


def test_empty_file_error_cannot_promote_error_body_to_scored_text():
    from harness.web_fetch.native_projection import require_native_answer_projection
    declaration = native_answer_projection('brightdata_unlocker', file_case(), {}, 502)
    metadata = {'native_answer_projection': declaration,
                'native_route_receipt': {'answer_fields': ['original_bytes']}}
    require_native_answer_projection('', metadata, b'Gateway error')
    with pytest.raises(ValueError):
        require_native_answer_projection('Gateway error', metadata, b'Gateway error')
    with pytest.raises(ValueError):
        require_native_answer_projection('', metadata, b'Gateway error',
            files=[{'data': b'Gateway error'}])
