import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.native_projection import (
    require_native_answer_projection, native_answer_projection, project_native_answer,
)
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch.providers.lexmount import LexmountProvider


def provider():
    return LexmountProvider('moli-api', {'type': 'lexmount', 'api_key': 'synthetic',
        'project_id': 'synthetic-project', 'engine': 'lightmount_domstable',
        'uniform_native_parameters': True})


def test_prior_projection_keeps_its_original_answer_scope():
    payload = {'result': {'main_text': 'Body', 'extra': {
        'dom_summary': {'headings': ['Heading']}, 'reviews': [{'text': 'Review'}]}}}
    old = native_answer_projection('lexmount', {}, payload, 200, policy_version=3)
    content, fields = project_native_answer(payload, old)
    assert content == 'Body'
    assert fields == {}  # No returned links or images: none declared.
    previous = native_answer_projection('lexmount', {}, payload, 200, policy_version=4)
    assert project_native_answer(payload, previous)[1] == {
        'answer_format': 'plain_text',
        'page_structure': {'headings': ['Heading']}, 'records': [{'text': 'Review'}]}
    current = native_answer_projection('lexmount', {}, payload, 200)
    assert 'page_information' in project_native_answer(payload, current)[1]


@pytest.mark.parametrize('extra,lead,breadcrumbs', [
    ({'dek': 'Article introduction', 'breadcrumb': ['Home', 'Essays']},
     'Article introduction', ['Home', 'Essays']),
    ({'lead': 'Primary introduction', 'dek': 'Alternate introduction',
      'breadcrumbs': ['Primary'], 'breadcrumb': ['Alternate']},
     'Primary introduction', ['Primary']),
    (None, None, []),
])
def test_extracted_field_aliases_are_selected_by_structure(extra, lead, breadcrumbs):
    payload = {'result': {'main_text': 'Body', 'extra': extra}}
    declaration = native_answer_projection('lexmount', {}, payload, 200)
    content, fields = project_native_answer(payload, declaration)
    assert content == 'Body'
    assert fields['page_information']['lead'] == lead
    assert fields['page_information']['breadcrumbs'] == breadcrumbs


@pytest.mark.parametrize('track,kind', [('web', 'body'), ('dynamic', 'body'),
    ('image', 'resource'), ('video', 'file'), ('document', 'body')])
def test_native_lexmount_keeps_uniform_request_and_replayable_answers(track, kind):
    case = EvalCase(id='sample', name='Sample', category='test',
                   url='https://example.com', expected_any=[], track=track, task_kind=kind)
    payload = {'result': {'main_text': 'Actual response', 'title': 'Page',
        'final_url': 'https://example.com/final', 'engine': 'lightmount_domstable',
        'links': [{'text': 'A', 'href': 'https://example.com/a'}],
        'images': [{'src': 'https://example.com/a.png'}],
        'extra': {'dom_summary': {'headings': ['Actual page heading']},
                  'reviews': [{'date': '2026-10-01', 'text': 'An actual review.'}]}}}
    raw = json.dumps(payload).encode()
    with patch('harness.web_fetch.providers.lexmount.request',
               return_value=HttpResponse(200, 'https://api.lexmount.com/v1/extract', {}, raw)) as request:
        result = provider().fetch(case, 30)
    assert result.ok
    assert result.metadata["answer_format"] == "plain_text"
    assert result.metadata['page_structure'] == payload['result']['extra']['dom_summary']
    assert result.metadata['records'] == payload['result']['extra']['reviews']
    assert request.call_args.kwargs['json_body'] == {
        'extract': {'url': case.url, 'options': {'engine_preference': 'lightmount_domstable'}},
        'trace': {'include_steps': True}}
    assert not result.files  # An extraction result is never invented file content.
    require_native_answer_projection(result.content, result.metadata, raw,
        title=result.title, final_url=result.final_url, request_url=case.url)
    with pytest.raises(ValueError):
        require_native_answer_projection('invented', result.metadata, raw,
            title=result.title, final_url=result.final_url, request_url=case.url)
    for field, replacement in [('page_structure', {'headings': ['Invented']}),
                               ('records', []), ('answer_format', 'html'),
                               ('page_information', {'title': 'Invented'})]:
        changed = deepcopy(result.metadata)
        changed[field] = replacement
        with pytest.raises(ValueError, match='field projection'):
            require_native_answer_projection(result.content, changed, raw,
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


def test_document_title_is_not_declared_as_extracted_page_information():
    from harness.web_fetch.native_projection import native_answer_projection, project_native_answer
    payload = {'result': {'main_text': 'Body', 'title': 'Document title', 'author': 'A. Writer'}}
    declaration = native_answer_projection('lexmount', {}, payload, 200)
    _, fields = project_native_answer(payload, declaration)
    assert 'title' not in fields['page_information']
    assert fields['page_information']['author'] == 'A. Writer'


BODY = 'This is the complete synthetic article body, delivered in full.'
NAVIGATION = 'Home | Products | About us | Contact | Sign in | Cookie settings'


def scored_fetch(case, result):
    """Fetch one synthetic response, then score it through the public predicate."""
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import score_attempt
    raw = json.dumps({'result': result}).encode()
    with patch('harness.web_fetch.providers.lexmount.request',
               return_value=HttpResponse(200, 'https://api.lexmount.com/v1/extract', {}, raw)):
        fetched = provider().fetch(case, 30)
    require_native_answer_projection(fetched.content, fetched.metadata, raw,
        title=fetched.title, final_url=fetched.final_url, request_url=case.url)
    metrics = score_attempt(case, fetched.content.encode(), ok=fetched.ok,
                            metadata=fetched.metadata, title=fetched.title, final_url=fetched.final_url)
    return fetched, task_passed(fetched.ok, metrics, case.task_kind)


def body_case(**fields):
    return EvalCase(id='sample', name='Sample', category='test', url='https://example.com/essay',
                    expected_any=[], **fields)


ANCHOR_CONTRACT = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                   'stable_text_anchors': [{'id': 'date', 'alternatives': ['May 2026']}]}
TITLE_CONTRACT = {'title_contract': {'version': 1, 'alternatives': ['H'], 'html_selector': 'h1'}}


@pytest.mark.parametrize('case,result,passed', [
    (body_case(acceptance_contract=ANCHOR_CONTRACT),
     {'main_text': BODY + ' May 2026.', 'publish_time': 1730000000}, True),
    (body_case(acceptance_contract=ANCHOR_CONTRACT),
     {'main_text': BODY + ' May 2026.', 'author': [{'name': 'A'}]}, True),
    (body_case(acceptance_contract=ANCHOR_CONTRACT),
     {'main_text': BODY + ' May 2026.', 'extra': {'subtitle': {'text': 'May 2026'}}}, True),
    # Malformed values are absent: they neither crash nor supply the anchor.
    (body_case(acceptance_contract=ANCHOR_CONTRACT),
     {'main_text': BODY, 'publish_time': 1730000000, 'extra': {'subtitle': {'text': 'May 2026'}}}, False),
    (body_case(source_benchmark=TITLE_CONTRACT),
     {'main_text': BODY, 'extra': {'dom_summary': {'headings': [{'level': 1, 'text': 'H'}]}}}, True),
    (body_case(source_benchmark=TITLE_CONTRACT),
     {'main_text': BODY, 'extra': {'dom_summary': 'H'}}, False),
    (body_case(acceptance_contract={'version': 1, 'schema': 'quote', 'minimum_records': 1,
                                    'stable_text_anchors': []}),
     {'main_text': '“A complete quotation” by Alex Writer Tags: books reading',
      'extra': {'reviews': {}}}, True),
])
def test_malformed_returned_fields_are_absent_not_errors(case, result, passed):
    fetched, task_pass = scored_fetch(case, result)
    assert task_pass is passed
    for value in fetched.metadata.get('page_information', {}).values():
        assert value is None or isinstance(value, str) or all(isinstance(item, str) for item in value)


@pytest.mark.parametrize('field', ['description', 'site_name'])
def test_document_metadata_cannot_supply_page_text(field):
    pattern = dict(ANCHOR_CONTRACT, stable_text_anchors=[], page_patterns=['May 2026'])
    for contract in (ANCHOR_CONTRACT, pattern):
        case = body_case(acceptance_contract=contract)
        result = {'main_text': NAVIGATION}
        result.update({field: 'May 2026'} if field == 'description' else {'extra': {field: 'May 2026'}})
        fetched, passed = scored_fetch(case, result)
        assert field not in fetched.metadata['page_information']
        assert passed is False
        # The same value as an extracted page field still counts.
        assert scored_fetch(case, {'main_text': NAVIGATION, 'extra': {'subtitle': 'May 2026'}})[1] is True


@pytest.mark.parametrize('author,kept', [
    (['A. Writer', 'B. Editor'], True),
    ('B. Editor', True),
    (['A. Writer', {'name': 'B. Editor'}], False),
    ({'name': 'B. Editor'}, False),
])
def test_text_lists_survive_and_other_shapes_are_absent(author, kept):
    contract = dict(ANCHOR_CONTRACT, stable_text_anchors=[{'id': 'by', 'alternatives': ['B. Editor']}])
    subtitle = ['May 2026', 'Essay'] if kept else 7
    fetched, passed = scored_fetch(body_case(acceptance_contract=contract),
                                   {'main_text': BODY, 'author': author, 'extra': {'subtitle': subtitle}})
    assert passed is kept
    information = fetched.metadata['page_information']
    assert information['author'] == (author if kept else None)
    assert information['subtitle'] == (subtitle if kept else None)


@pytest.mark.parametrize('result', [
    {'main_text': BODY, 'title': ['Heading'], 'final_url': {'href': 'https://example.com/essay'}},
    {'main_text': BODY, 'title': 7, 'url': ['https://example.com/essay']},
])
def test_non_text_title_and_address_are_absent(result):
    fetched, _ = scored_fetch(body_case(), result)
    assert fetched.title is None and fetched.final_url is None
