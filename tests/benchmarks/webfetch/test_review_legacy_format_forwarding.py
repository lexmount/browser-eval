"""Legacy list gates share the answer's declared representation."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.list_verification import verify_list


CONTRACT = {'mode': 'dynamic_list_structure', 'hosts': ['example.test'],
            'entry_path': r'/item/\d+', 'minimum_entries': 1,
            'verification_scope': 'first_three_name_detail_pairs'}
CASE = {'url': 'https://example.test/', 'task_kind': 'structure', 'source_benchmark': CONTRACT}
REFERENCE = {'case_id': 'format-pairs', 'items': [{'url': 'https://example.test/item/1', 'title': 'Example'}]}
LINKS = [{'url': 'https://example.test/item/1', 'title': 'Example'}]


SCENARIOS = [
    ('literal-markdown', '[Example](/item/1)', 'plain_text', None, False),
    ('rendered-markdown', '[Example](/item/1)', 'markdown', None, True),
    ('literal-html', '<a href="/item/1">Example</a>', 'plain_text', None, False),
    ('rendered-html', '<a href="/item/1">Example</a>', 'html', None, True),
    ('plain-field-lines', 'Example\nhttps://example.test/item/1', 'plain_text', None, True),
    ('plain-explicit-metadata', 'Example', 'plain_text', LINKS, True),
]


@pytest.mark.parametrize('identifier,text,representation,links,expected', SCENARIOS)
def test_legacy_score_and_reference_gate_receive_answer_format(identifier, text, representation, links, expected):
    metadata = {'answer_format': representation}
    if links:
        metadata['links'] = links
    result = evaluate_response(CASE, text.encode(), metadata=metadata, list_reference=REFERENCE)
    assert result['measured']
    assert result['passed'] is expected
    assert result['metrics']['structure_pass'] is expected
    assert result['metrics']['list_task_pass'] is expected


@pytest.mark.parametrize('via_metadata', [False, True])
@pytest.mark.parametrize('identifier,text,representation,links,expected', SCENARIOS)
def test_direct_verification_accepts_declared_format_without_parsing_literal_markup(identifier, text, representation, links, expected, via_metadata):
    kwargs = {'metadata': {'answer_format': representation}} if via_metadata else {'representation': representation}
    result = verify_list(text, CONTRACT, CASE['url'], REFERENCE, links=links, **kwargs)
    assert result['list_task_pass'] is expected


def scoring_controls():
    for identifier, text, representation, links, expected in SCENARIOS:
        metadata = {'answer_format': representation}
        if links:
            metadata['links'] = links
        yield {'id': 'legacy-answer-format-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Legacy structure and independent-list verification must forward the validated representation rather than infer markup from literal text.',
               'case': CASE, 'artifact': text, 'ok': True, 'metadata': metadata,
               'list_reference': REFERENCE, 'expected_task_pass': expected}
