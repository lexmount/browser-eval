import json
from unittest.mock import patch

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.native_projection import require_native_answer_projection
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch.providers.lexmount import LexmountProvider


def provider():
    return LexmountProvider('moli-api', {'type': 'lexmount', 'api_key': 'synthetic',
        'project_id': 'synthetic-project', 'engine': 'lightmount_domstable',
        'uniform_native_parameters': True})


@pytest.mark.parametrize('track,kind', [('web', 'body'), ('dynamic', 'body'),
    ('image', 'resource'), ('video', 'file'), ('document', 'body')])
def test_native_lexmount_keeps_uniform_request_and_replayable_answers(track, kind):
    case = EvalCase(id='sample', name='Sample', category='test',
                   url='https://example.com', expected_any=[], track=track, task_kind=kind)
    payload = {'result': {'main_text': 'Actual response', 'title': 'Page',
        'final_url': 'https://example.com/final', 'engine': 'lightmount_domstable',
        'links': [{'text': 'A', 'href': 'https://example.com/a'}],
        'images': [{'src': 'https://example.com/a.png'}]}}
    raw = json.dumps(payload).encode()
    with patch('harness.web_fetch.providers.lexmount.request',
               return_value=HttpResponse(200, 'https://api.lexmount.com/v1/extract', {}, raw)) as request:
        result = provider().fetch(case, 30)
    assert result.ok
    assert request.call_args.kwargs['json_body'] == {
        'extract': {'url': case.url, 'options': {'engine_preference': 'lightmount_domstable'}},
        'trace': {'include_steps': True}}
    assert not result.files  # An extraction result is never invented file content.
    require_native_answer_projection(result.content, result.metadata, raw,
        title=result.title, final_url=result.final_url, request_url=case.url)
    with pytest.raises(ValueError):
        require_native_answer_projection('invented', result.metadata, raw,
            title=result.title, final_url=result.final_url, request_url=case.url)


@pytest.mark.parametrize('status,payload', [(401, {'error': {'message': 'Unauthorized'}}),
    (200, {}), (200, {'result': {'engine': 'lightmount_domstable'}})])
def test_native_lexmount_never_scores_error_envelope_as_text(status, payload):
    case = EvalCase(id='sample', name='Sample', category='test',
                   url='https://example.com', expected_any=[])
    raw = json.dumps(payload).encode()
    with patch('harness.web_fetch.providers.lexmount.request',
               return_value=HttpResponse(status, 'https://api.lexmount.com/v1/extract', {}, raw)):
        result = provider().fetch(case, 30)
    assert result.content == ''
    if status == 401:
        assert not result.ok
        assert result.error_type == 'authentication'
    require_native_answer_projection(result.content, result.metadata, raw,
        title=result.title, final_url=result.final_url, request_url=case.url)


def test_native_lexmount_rejects_unrecognized_engine_before_call():
    instance = provider()
    instance.config['engine'] = 'invented-done'
    case = EvalCase(id='sample', name='Sample', category='test',
                   url='https://example.com', expected_any=[])
    with patch('harness.web_fetch.providers.lexmount.request') as request:
        with pytest.raises(ValueError, match='engine is unsupported'):
            instance.fetch(case, 30)
        request.assert_not_called()


def test_native_auto_can_omit_optional_trace():
    instance = provider()
    instance.config.update(engine='auto', trace_include_steps=False)
    case = EvalCase(id='sample', name='Sample', category='test',
                   url='https://example.com', expected_any=[])
    response = HttpResponse(200, case.url, {}, b'{"result":{"main_text":"body"}}')
    with patch('harness.web_fetch.providers.lexmount.request', return_value=response) as request:
        result = instance.fetch(case, 30)
    assert result.content == 'body'
    assert request.call_args.kwargs['json_body'] == {
        'extract': {'url': case.url, 'options': {'engine_preference': 'auto'}}}


@pytest.mark.parametrize('value', ['false', 0, None])
def test_native_trace_rejects_non_boolean_before_call(value):
    instance = provider()
    instance.config['trace_include_steps'] = value
    case = EvalCase(id='sample', name='Sample', category='test',
                   url='https://example.com', expected_any=[])
    with patch('harness.web_fetch.providers.lexmount.request') as request:
        with pytest.raises(ValueError, match='must be boolean'):
            instance.fetch(case, 30)
        request.assert_not_called()


def test_native_nested_input_rejects_url_drift_and_snapshot_reuse():
    from harness.web_fetch.native_routes import NativeRouteLedger, normalized_http_parameters
    case = EvalCase(id='unicode', name='Unicode', category='test',
        url='https://example.com/中文', expected_any=[],
        source_benchmark={'input_media_kind': 'html_page'})
    instance = provider()
    ledger = NativeRouteLedger([case], [instance], timeout_seconds=30)
    assert len(ledger.cells) == 1
    _, args = instance.native_http_arguments(case, 30)
    assert '%E4%B8%AD' in args['json_body']['extract']['url']
    assert normalized_http_parameters(case, args)['body']['extract']['url'] == '<input-url>'
    args['json_body']['extract']['dom_id'] = 'previous-snapshot'
    with pytest.raises(ValueError, match='frozen task URL'):
        normalized_http_parameters(case, args)
    del args['json_body']['extract']['dom_id']
    args['json_body']['extract']['url'] = 'https://other.example/'
    with pytest.raises(ValueError, match='frozen task URL'):
        normalized_http_parameters(case, args)


@pytest.mark.parametrize('status,raw', [
    (200, json.dumps({'result': {'main_text': 'Body', 'links': None, 'images': None}}).encode()),
    (502, b'<html><body>Bad gateway</body></html>'),
])
def test_native_lexmount_replays_null_resources_and_non_json_failures(status, raw):
    case = EvalCase(id='sample', name='Sample', category='test',
                    url='https://example.com', expected_any=[])
    with patch('harness.web_fetch.providers.lexmount.request',
               return_value=HttpResponse(status, 'https://api.lexmount.com/v1/extract', {}, raw)):
        result = provider().fetch(case, 30)
    assert result.ok is (status == 200)
    require_native_answer_projection(result.content, result.metadata, raw,
        title=result.title, final_url=result.final_url, request_url=case.url)
    # Saved-evidence replay derives the same declaration from the raw bytes.
    from harness.web_fetch.native_projection import native_projection_policy, native_projection_policy_version
    from harness.web_fetch.native_routes import _expected_native_declaration
    record = {'case_id': case.id, 'url': case.url, 'provider_type': 'lexmount', 'status_code': status,
              'provider_metadata': {**result.metadata, 'native_route_receipt': {'requests': []}}}
    version = native_projection_policy_version(native_projection_policy('lexmount'), 'lexmount')
    assert _expected_native_declaration(None, record, raw, {case.id: {'url': case.url}},
                                        policy_version=version) == result.metadata.get('native_answer_projection')
