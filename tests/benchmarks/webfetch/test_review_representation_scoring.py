"""Declared representations select prose interpretation, not delivered evidence."""
import hashlib

import pytest

from lexbench_webfetch.evaluation import evaluate_response


def _case(**requirements):
    return {'url': 'https://example.test/', **requirements}


def _pattern(pattern, scope='visible_text'):
    return {'required_patterns': [{'id': 'required', 'pattern': pattern, 'match_scope': scope}]}


CASES = [
    ('plain-frontmatter', '---\nsecret: prohibited\n---\nGood body', 'plain_text', {'forbidden_any': ['prohibited']}, False),
    ('markdown-frontmatter', '---\nsecret: prohibited\n---\nGood body', 'markdown', {'forbidden_any': ['prohibited']}, True),
    ('visible-label', '[Order ID 123](https://example.test/)', 'markdown', _pattern('Order ID 123'), True),
    ('image-alt', 'Good body ![Order ID 123](image.png)', 'markdown', _pattern('Order ID 123'), False),
    ('link-destination', 'Good body [Open](https://example.test/Order-ID-123)', 'markdown', _pattern('Order-ID-123'), False),
    ('link-title', 'Good body [Open](https://example.test/ "Order ID 123")', 'markdown', _pattern('Order ID 123'), False),
    ('literal-code', '`![Order ID 123](image.png)`', 'markdown', _pattern('Order ID 123'), True),
    ('formatted-label', '`Order ID 123`', 'markdown', _pattern('^Order ID 123$'), True),
    ('output-metadata', 'Good body ![Order ID 123](image.png)', 'markdown', _pattern('Order ID 123', 'output'), True),
    ('plain-image', '![Order ID 123](image.png)', 'plain_text', {'required_facts': [{'id': 'order', 'alternatives': ['Order ID 123']}]}, True),
    ('plain-destination', '[Open](https://example.test/prohibited)', 'plain_text', {'forbidden_any': ['prohibited']}, False),
    ('markdown-destination', '[Open](https://example.test/prohibited)', 'markdown', {'forbidden_any': ['prohibited']}, True),
    ('plain-html', '<span hidden>Order ID 123</span>', 'plain_text', _pattern('Order ID 123'), True),
    ('html-hidden', '<span hidden>Order ID 123</span>', 'html', _pattern('Order ID 123'), False),
    ('plain-emphasis', '**Required label**', 'plain_text', _pattern(r'^\*\*Required label\*\*$'), True),
    ('plain-frontmatter-pattern', '---\nsecret: prohibited\n---\nGood body', 'plain_text', _pattern('prohibited'), True),
    ('plain-json', '{"success":true,"result":"Order ID 123"}', 'plain_text', _pattern('Order ID 123'), True),
    ('native-json', '{"success":true,"result":"Order ID 123"}', 'markdown', _pattern('Order ID 123'), True),
]


@pytest.mark.parametrize('identifier,text,representation,requirements,expected', CASES)
def test_declared_representation_controls_prose_scoring(identifier, text, representation, requirements, expected):
    result = evaluate_response(_case(**requirements), text.encode(), metadata={'answer_format': representation})
    assert result['passed'] is expected


@pytest.mark.parametrize('text', ['a < b', '&literal;', '你好 <世界>', '![diagram](image.png)'])
def test_delivered_character_count_and_hash_are_not_temporary_projections(text):
    metrics = evaluate_response(_case(), text.encode(), metadata={'answer_format': 'plain_text'})['metrics']
    assert metrics['content_chars'] == len(text)
    assert metrics['content_sha256'] == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize('representation,expected', [('plain_text', True), ('markdown', False)])
def test_plain_text_stable_anchor_retains_literal_destination(representation, expected):
    contract = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'stable_text_anchors': [{'id': 'evidence', 'alternatives': ['special evidence']}],
                'stable_text_role': 'required'}
    result = evaluate_response(_case(acceptance_contract=contract),
                               b'[Open](https://example.test/ "special evidence")',
                               metadata={'answer_format': representation})
    assert result['passed'] is expected


def _body_contract(version):
    body = {'version': version, 'unit': ('region_token_recall' if version == 1 else
                                        'region_compact_trigram_recall' if version == 2 else
                                        'region_compact_hexagram_recall'),
            'minimum_coverage': 1, 'regions': [{'id': 'literal', 'text': 'special evidence'}]}
    if version >= 3:
        body.update(minimum_floor=1, minimum_region_passes=1)
    if version >= 4:
        body['ending'] = {'text': 'special evidence'}
    return body


@pytest.mark.parametrize('version', [1, 2, 3, 4, 5])
@pytest.mark.parametrize('representation,expected', [('plain_text', True), ('markdown', False)])
def test_plain_text_body_regions_retain_literal_destination(representation, expected, version):
    result = evaluate_response(_case(body_requirements=_body_contract(version)),
                               b'[Open](https://example.test/special-evidence)',
                               metadata={'answer_format': representation})
    assert result['passed'] is expected


def test_plain_answer_keeps_independent_markdown_reference_semantics():
    result = evaluate_response(_case(ground_truth_main_content='[Required prose](https://source.test/hidden)'),
                               b'Required prose', metadata={'answer_format': 'plain_text'})
    assert result['metrics']['word_f1'] == 1


@pytest.mark.parametrize('control', list(range(5)))
def test_raw_character_count_controls(control):
    row = list(length_controls())[control]
    result = evaluate_response(row['case'], row['artifact'].encode(), metadata=row['metadata'],
                               status_code=row.get('status_code'))
    assert result['passed'] is row['expected_task_pass']
    for key, expected in row['expected_metrics'].items():
        assert result['metrics'][key] == expected


def length_controls():
    for index, text in enumerate(['a < b', '&literal;', '你好 <世界>', '![diagram](image.png)']):
        yield {'id': f'runtime7-delivered-length-{index}', 'category': 'answer-surface',
               'failure_mode': 'false-negative', 'root_cause': 'persisted_output_character_count',
               'case': _case(), 'artifact': text, 'metadata': {'answer_format': 'plain_text'},
               'ok': True, 'expected_task_pass': True, 'expected_metrics': {'content_chars': len(text)}}
    text = '{"success":true,"result":""}'
    yield {'id': 'runtime7-raw-api-length', 'category': 'answer-surface',
           'failure_mode': 'false-positive', 'root_cause': 'persisted_output_character_count',
           'case': _case(), 'artifact': text, 'ok': True, 'status_code': 200,
           'metadata': {'native_answer_projection': {'schema_version': 2, 'content_pointer': ['result'],
                                                      'content_format': 'text'},
                        'raw_response_sha256': hashlib.sha256(text.encode()).hexdigest()},
           'expected_task_pass': False, 'expected_metrics': {'content_chars': len(text), 'request_ok': True}}


def scoring_controls():
    for identifier, text, representation, requirements, expected in CASES:
        yield {'id': 'runtime7-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Declared plain text retains literal content; Markdown visible-text requirements use rendered prose while output scopes retain delivered syntax.',
               'case': _case(**requirements), 'artifact': text, 'metadata': {'answer_format': representation},
               'ok': True, 'expected_task_pass': expected}
    for representation, expected in [('plain_text', True), ('markdown', False)]:
        for version in range(1, 6):
            yield {'id': f'runtime7-body-{version}-{representation}', 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'declared_plain_text_body_coverage',
                   'case': _case(body_requirements=_body_contract(version)),
                   'artifact': '[Open](https://example.test/special-evidence)', 'ok': True,
                   'metadata': {'answer_format': representation}, 'expected_task_pass': expected}
        contract = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                    'stable_text_anchors': [{'id': 'evidence', 'alternatives': ['special evidence']}],
                    'stable_text_role': 'required'}
        yield {'id': f'runtime7-stable-{representation}', 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'declared_plain_text_stable_anchors', 'case': _case(acceptance_contract=contract),
               'artifact': '[Open](https://example.test/ "special evidence")', 'ok': True,
               'metadata': {'answer_format': representation}, 'expected_task_pass': expected}
    yield from length_controls()
