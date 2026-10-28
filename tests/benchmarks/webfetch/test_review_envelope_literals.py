"""Link metadata, literal examples and transport envelopes retain their owners."""
import hashlib
import re

import pytest

from harness.web_fetch.call_boundary import prepare_answer
from harness.web_fetch.models import ProviderResult
from lexbench_webfetch.dynamic_lists import extract_named_entries
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.reference_quality import markdown_links, without_markdown_images
from lexbench_webfetch.response_input import answer_input, apply_request_status


TITLE_SCOPE = {'html_selector': 'table', 'markdown_title_identity_pattern': r'javascript:show\((\d+)\)',
               'markdown_detail_identity_pattern': r'/item/(\d+)'}
CONTRACT = {'version': 1, 'schema': 'named_link', 'minimum_records': 1,
            'hosts': ['example.com'], 'entry_path': r'/item/\d+', 'stable_text_anchors': [],
            'entry_scope': TITLE_SCOPE}


@pytest.mark.parametrize('bracketed', [False, True])
@pytest.mark.parametrize('quote', ['"', "'"])
@pytest.mark.parametrize('matching', [False, True])
def test_optional_link_title_binds_only_a_matching_visible_title_cell(bracketed, quote, matching):
    destination = '<https://example.com/item/1>' if bracketed else 'https://example.com/item/1'
    link = f'[Details]({destination} {quote}Alpha Details{quote})'
    parsed = markdown_links(link)[0]
    assert parsed.title == 'Alpha Details'
    text = '| ' + ('Alpha' if matching else 'Other title') + ' | ' + link + ' |'
    found = extract_named_entries(text, CONTRACT, 'https://example.com')
    assert bool(found) is matching
    assert evaluate_response({'url': 'https://example.com', 'acceptance_contract': CONTRACT},
                             text.encode())['passed'] is matching


@pytest.mark.parametrize('literal', [False, True])
@pytest.mark.parametrize('fenced', [False, True])
def test_literal_image_spelling_is_visible_but_real_image_alt_is_not(literal, fenced):
    text = '![diagram](example.png)'
    if literal:
        text = '```markdown\n' + text + '\n```' if fenced else '`' + text + '`'
    assert ('diagram' in without_markdown_images(text)) is literal
    case = {'url': 'https://example.test/', 'acceptance_contract': {
        'version': 1, 'schema': 'body_text', 'minimum_records': 0,
        'stable_text_anchors': [{'id': 'example', 'alternatives': ['diagram']}]}}
    assert evaluate_response(case, text.encode())['passed'] is literal


RAW = b'{"success":true,"result":"","message":"Required fact"}'


def raw_envelope_metadata(raw=RAW):
    return {'answer_format': 'plain_text', 'raw_response_sha256': hashlib.sha256(raw).hexdigest(),
            'native_answer_projection': {'schema_version': 2, 'content_pointer': ['result'],
                                         'content_format': 'text', 'optional_content': True}}


@pytest.mark.parametrize('declared', [False, True])
@pytest.mark.parametrize('ok', [False, True])
def test_identical_json_is_page_content_unless_raw_envelope_provenance_is_bound(declared, ok):
    metadata = raw_envelope_metadata() if declared else {'answer_format': 'plain_text'}
    text, _, request_ok, _ = answer_input(RAW, metadata, ok=ok, status_code=200)
    assert text == ('' if declared else RAW.decode())
    assert request_ok is ok
    result = evaluate_response({'url': 'https://example.test/', 'required_facts': [
        {'alternatives': ['Required fact']}]}, RAW, metadata=metadata, ok=ok, status_code=200)
    assert result['passed'] is (ok and not declared)


@pytest.mark.parametrize('defect', ['hash', 'pointer', 'format'])
def test_unbound_projection_cannot_discard_a_native_json_document(defect):
    metadata = raw_envelope_metadata()
    if defect == 'hash':
        metadata['raw_response_sha256'] = '0' * 64
    else:
        metadata['native_answer_projection']['content_' + defect] = ['content'] if defect == 'pointer' else 'json'
    assert answer_input(RAW, metadata, ok=True, status_code=200)[0] == RAW.decode()


def test_empty_service_envelope_cannot_reverse_an_origin_error_or_clear_its_status():
    metadata = {**raw_envelope_metadata(), 'upstream_status': 503}
    result = prepare_answer(ProviderResult(True, RAW.decode(), status_code=200, metadata=metadata),
                            EvalCase('request', 'Request', 'https://example.test/', 'web', []))
    assert result.ok is False
    assert result.prepared_answer[2] is False
    record = {'ok': result.ok, 'error_type': result.error_type, 'error_message': result.error_message,
              'provider_metadata': metadata, 'metrics': {'request_status_basis': result.prepared_answer[3]}}
    apply_request_status(record)
    assert record['ok'] is False
    assert record['error_type'] == 'http_error'
    assert record['error_message'] == 'Target page returned HTTP 503'


@pytest.mark.parametrize('answer,expected', [
    ('01 #include <stdio.h>', True), ('#include <stdio.h>', True),
    ('01 #include <stdlib.h>', False),
    ('<div hidden>\n01 #include <stdio.h>\n</div>', False),
    ('<a href="#include<stdio.h>">Other text</a>', False),
])
def test_code_component_preserves_header_tokens_without_unhiding_markup(answer, expected):
    case = {'url': 'https://example.test/', 'required_patterns': [
        {'id': 'include', 'match_scope': 'code_compact', 'pattern': re.escape('#include<stdio.h>')}]}
    assert evaluate_response(case, answer.encode())['passed'] is expected


def scoring_controls():
    def row(identifier, case, text, expected, cause, **extra):
        return {'id': 'runtime6-' + identifier, 'category': 'answer-surface',
                'failure_mode': 'false-negative' if expected else 'false-positive',
                'root_cause': cause, 'case': {'url': 'https://example.test/', **case},
                'artifact': text, 'ok': True, 'expected_task_pass': expected, **extra}
    for bracketed in (False, True):
        destination = '<https://example.com/item/1>' if bracketed else 'https://example.com/item/1'
        for matching in (False, True):
            text = '| ' + ('Alpha' if matching else 'Other title') + f' | [Details]({destination} "Alpha Details") |'
            yield row(f'angle-title-{bracketed}-{matching}',
                      {'url': 'https://example.com', 'acceptance_contract': CONTRACT}, text, matching,
                      'Bracketed and ordinary Markdown destinations retain the same optional title, which binds only a separately visible matching name.')
    body = {'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                                   'stable_text_anchors': [{'id': 'example', 'alternatives': ['diagram']}]}}
    for name, text, expected in [('image', '![diagram](example.png)', False),
                                 ('inline-code', '`![diagram](example.png)`', True),
                                 ('fenced-code', '```markdown\n![diagram](example.png)\n```', True)]:
        yield row(name, body, text, expected,
                  'Image metadata is excluded, but image syntax displayed in code remains literal visible prose through later normalization.')
    task = {'required_facts': [{'alternatives': ['Required fact']}]}
    for declared in (False, True):
        for ok in (False, True):
            metadata = raw_envelope_metadata() if declared else {'answer_format': 'plain_text'}
            yield row(f'envelope-{declared}-{ok}', task, RAW.decode(), ok and not declared,
                      'Only adapter-declared raw API bytes with matching provenance may be projected; page JSON and adapter failure status stay intact.',
                      metadata=metadata, ok=ok, status_code=200, expected_metrics={'request_ok': ok})
    for answer, expected in [('01 #include <stdio.h>', True), ('#include <stdio.h>', True),
                             ('01 #include <stdlib.h>', False),
                             ('<div hidden>\n01 #include <stdio.h>\n</div>', False),
                             ('<a href="#include<stdio.h>">Other text</a>', False)]:
        case = {'required_patterns': [{'id': 'include', 'match_scope': 'code_compact',
                                       'pattern': re.escape('#include<stdio.h>')}]}
        yield row('code-header-' + str(len(answer)) + '-' + str(expected), case, answer, expected,
                  'The code-component matcher preserves explicit preprocessor header tokens without exposing hidden HTML or attribute values.')
