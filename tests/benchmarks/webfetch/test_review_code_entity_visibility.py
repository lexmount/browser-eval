"""Markdown code keeps literal payloads while prose renders one entity layer."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response


def case(value):
    return {'url': 'https://example.test/', 'acceptance_contract': {'version': 1,
        'schema': 'body_text', 'minimum_records': 0, 'page_patterns': ['^' + re.escape(value) + r'\s*$']}}


def cases():
    for kind, wrap in [('inline', lambda s: '`' + s + '`'),
                       ('fence', lambda s: '```text\n' + s + '\n```'),
                       ('indent', lambda s: '    ' + s)]:
        for value in ['&amp;', '&amp;amp;', r'\&amp;', '**&amp;**', r'\*literal\*']:
            yield kind + '-literal-' + value, wrap(value), value, 'markdown', True
        yield kind + '-not-decoded', wrap('&amp;'), '&', 'markdown', False
    for source, rendered in [('&amp;', '&'), ('&amp;amp;', '&amp;'), (r'path\:value', 'path:value')]:
        yield 'prose-' + source, source, rendered, 'markdown', True
    yield 'prose-not-double-decoded', '&amp;amp;', '&', 'markdown', False
    yield 'fence-trailing-backslash', '```text\npath\\\nnext\n```', 'path\\\nnext', 'markdown', True
    yield 'inline-soft-line-backslash', '`path\\\nnext`', 'path\\ next', 'markdown', True
    yield 'prose-hard-break', 'path\\\nnext', 'path\nnext', 'markdown', True
    yield 'html-once', '<p>&amp;amp;</p>', '&amp;', 'html', True
    yield 'html-code-once', '<code>&amp;amp;</code>', '&amp;', 'html', True
    yield 'plain-literal', '&amp;', '&amp;', 'plain_text', True
    yield 'plain-backticks-literal', '`&amp;`', '&amp;', 'plain_text', False
    yield 'many-code-spans', ' '.join('`&amp;`' for _ in range(12)), ' '.join('&amp;' for _ in range(12)), 'markdown', True
    for kind, source, shown in [
        ('escaped-stars', r'\*literal\*', '*literal*'),
        ('real-emphasis', '*literal*', 'literal'),
        ('named-stars', '&ast;literal&ast;', '*literal*'),
        ('numeric-stars', '&#42;literal&#42;', '*literal*'),
        ('escaped-amp', r'\&amp;', '&amp;'),
        ('double-amp', '&amp;amp;', '&amp;'),
        ('emitted-ticks', '&#96;literal&#96;', '`literal`'),
        ('escaped-ticks', r'\`literal\`', '`literal`'),
        ('emitted-link', '&lbrack;literal&rbrack;(/other)', '[literal](/other)'),
    ]:
        yield 'prose-new-' + kind, source, shown, 'markdown', True
        wrong = 'literal' if shown != 'literal' and 'literal' in shown else ('*literal*' if shown == 'literal' else '&')
        yield 'prose-new-' + kind + '-distinct', source, wrong, 'markdown', False


@pytest.mark.parametrize('identifier,source,value,representation,expected', list(cases()))
def test_code_payload_and_prose_entity_layers_remain_distinct(identifier, source, value, representation, expected):
    result = evaluate_response(case(value), source.encode(), metadata={'answer_format': representation})
    assert result['measured']
    assert result['passed'] is expected


@pytest.mark.parametrize('source,shown', [(r'\*literal\*', '*literal*'), ('&ast;literal&ast;', '*literal*'),
    ('&#96;literal&#96;', '`literal`'), (r'\&amp;', '&amp;'), ('*literal*', 'literal')])
def test_completed_body_anchor_uses_visible_characters_without_reparsing(source, shown):
    contract = case(shown)
    contract['acceptance_contract'].pop('page_patterns')
    contract['acceptance_contract']['stable_text_anchors'] = [{'id': 'shown', 'alternatives': [
        '\\*literal\\*' if shown == '*literal*' else (r'\`literal\`' if shown == '`literal`' else (r'\&amp;' if shown == '&amp;' else shown))]}]
    assert evaluate_response(contract, source.encode(), metadata={'answer_format': 'markdown'})['passed']


def test_entity_encoded_internal_marker_prefix_does_not_collide():
    source = '&#57382;stable-code0&#57383; ' + r'\*literal\*'
    assert evaluate_response(case('\ue026stable-code0\ue027 *literal*'), source.encode(), metadata={'answer_format': 'markdown'})['passed']


@pytest.mark.parametrize('source,expected', [('_:_Daily', True), ('_:_Dailybreak', False)])
def test_native_punctuation_boundary_keeps_whole_label_matching(source, expected):
    contract = {'url': 'https://example.test/', 'acceptance_contract': {'version': 1, 'schema': 'body_text',
        'minimum_records': 0, 'stable_text_anchors': [{'id': 'label', 'alternatives': ['Daily']}]}}
    assert evaluate_response(contract, source.encode())['passed'] is expected


@pytest.mark.parametrize('source,shown', [('_:_Daily', ': Daily'), (r'\_:\_Daily', '_:_Daily'),
    ('`_:_Daily`', '_:_Daily'), ('&lowbar;:&lowbar;Daily', '_:_Daily')])
def test_only_original_native_punctuation_markup_establishes_a_boundary(source, shown):
    assert evaluate_response(case(shown), source.encode(), metadata={'answer_format': 'markdown'})['passed']
    other = '_:_Daily' if shown == ': Daily' else ': Daily'
    assert not evaluate_response(case(other), source.encode(), metadata={'answer_format': 'markdown'})['passed']


def scoring_controls():
    selected = {'inline-literal-&amp;', 'inline-not-decoded', 'fence-literal-&amp;', 'fence-not-decoded',
                'prose-&amp;', 'prose-not-double-decoded', 'fence-trailing-backslash', 'inline-soft-line-backslash'}
    for identifier, source, value, representation, expected in cases():
        if identifier in selected or identifier in {'prose-new-escaped-stars', 'prose-new-real-emphasis',
                'prose-new-escaped-stars-distinct', 'prose-new-named-stars', 'prose-new-named-stars-distinct',
                'prose-new-emitted-ticks', 'prose-new-emitted-ticks-distinct',
                'prose-new-numeric-stars', 'prose-new-escaped-amp', 'prose-new-escaped-amp-distinct'}:
            yield {'id': 'code21-' + identifier, 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Literal Markdown code is shielded through prose entity, escape, hard-break and formatting transforms.',
                   'case': case(value), 'artifact': source, 'metadata': {'answer_format': representation},
                   'ok': True, 'expected_task_pass': expected}
