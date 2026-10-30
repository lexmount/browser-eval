"""HTML attributes own their delimiters independently of nearby comments."""
import re

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.reference_quality import (
    _markdown_html_tag_spans, markdown_literal_spans, visible_mixed_html_text,
)
from lexbench_webfetch.scoring import score_attempt


CASES = [
    ('visible-code', 'Intro <span title="`">hello `target`</span>', {'target': True, '">hello': False}),
    ('visible-code-comment', 'Intro <span title="`">hello `target`</span><!-- noise -->', {'target': True, '">hello': False}),
    ('attribute-only', 'Intro <span title="`target`">hello</span>', {'target': False}),
    ('attribute-only-comment', 'Intro <span title="`target`">hello</span><!-- noise -->', {'target': False}),
    ('single-quoted', "Intro <span title='`'>hello `target`</span>", {'target': True, "'>hello": False}),
    ('quoted-greater', 'Intro <span title="x > `">hello `target`</span>', {'target': True}),
    ('double-backticks', 'Intro <span title="``">hello ``target``</span>', {'target': True}),
    ('earlier-code', '`<span title="` <span title="`">hello `target`</span>',
     {'target': True, '<spantitle="': True, '">hello': False}),
    ('escaped-tag', r'Intro \<span title="`">hello `target`</span>', {'target': False, '">hello': True}),
    ('real-comment', 'Intro <span title="`">hello</span><!-- `target` -->', {'target': False}),
    ('attribute-comment', 'Intro <span title="`<!--`">hello `target`</span>', {'target': True}),
]


def controls():
    for identifier, source, expectations in CASES:
        for expected, passed in expectations.items():
            for representation in [None, 'markdown']:
                yield {'id': 'attribute-code-' + identifier + '-' + str(list(expectations).index(expected))
                       + '-' + (representation or 'inferred'),
                       'root_cause': '4410744429', 'category': 'answer-surface',
                       'failure_mode': 'false-negative' if passed else 'false-positive',
                       'case': {'url': 'https://example.test/', 'required_patterns': [
                           {'id': 'source', 'match_scope': 'code_compact', 'pattern': '^' + re.escape(expected) + '$'}]},
                       'artifact': source, 'ok': True,
                       'metadata': {'answer_format': representation} if representation else {},
                       'expected_task_pass': passed}


CONTROLS = list(controls())


@pytest.mark.parametrize('row', CONTROLS, ids=[row['id'] for row in CONTROLS])
def test_attribute_code_ownership_through_public_scorer(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(True, metrics, 'body') is row['expected_task_pass']


@pytest.mark.parametrize('comment', ['', '<!-- noise -->'])
def test_mixed_projection_preserves_visible_code_and_removes_attribute_payload(comment):
    source = '<span title="`">hello `target`</span>' + comment
    assert visible_mixed_html_text(source) == 'hello `target`'
    assert [source[start:end] for start, end in markdown_literal_spans(source)] == ['`target`']


def test_earlier_code_keeps_ownership_before_later_html_attributes():
    source = '`<span title="` <span title="`">hello `target`</span>'
    assert [source[start:end] for start, end in markdown_literal_spans(source)] == ['`<span title="`', '`target`']


DECLARATIONS = [
    '<![UNRECOGNIZED[ XML source ]]>', '<![%draft;[\n<!ELEMENT note ANY>\n]]>',
    '<![CDATA[ XML source ]]>', '<![IGNORE[ XML source ]]>', '<![INCLUDE[ XML source ]]>',
    '<?xml version="1.0"?>',
]


@pytest.mark.parametrize('declaration', DECLARATIONS)
@pytest.mark.parametrize('position', ['before', 'after'])
def test_declaration_ranges_preserve_original_offsets_and_visible_code(declaration, position):
    markup = '<span title="`">hello `target`</span>'
    source = 'Intro ' + (declaration + markup if position == 'before' else markup + declaration)
    opening = source.index('<span')
    assert (opening, source.index('hello')) in _markdown_html_tag_spans(source)
    assert [source[start:end] for start, end in markdown_literal_spans(source)] == ['`target`']
    for expected, passed in [('target', True), ('">hello', False)]:
        case = {'url': 'https://example.test/', 'required_patterns': [
            {'id': 'source', 'match_scope': 'code_compact', 'pattern': '^' + re.escape(expected) + '$'}]}
        metrics = score_attempt(case, source.encode(), ok=True, metadata={'answer_format': 'markdown'})
        assert task_passed(True, metrics, 'body') is passed


@pytest.mark.parametrize('declaration', DECLARATIONS)
def test_literal_declarations_keep_earlier_code_and_attribute_ownership(declaration):
    literal = '`' + declaration + '`'
    source = literal + ' <span title="`">hello `target`</span>'
    assert [source[start:end] for start, end in markdown_literal_spans(source)] == [literal, '`target`']
    source = 'Intro <span title="' + declaration.replace('"', '&quot;') + ' `">hello `target`</span>'
    assert [source[start:end] for start, end in markdown_literal_spans(source)] == ['`target`']
