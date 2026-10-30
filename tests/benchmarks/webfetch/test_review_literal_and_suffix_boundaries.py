"""Literal spans stay disjoint and final link labels keep shared boundaries."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.reference_quality import (
    markdown_literal_spans, visible_markdown_links, visible_mixed_html_text,
)


@pytest.mark.parametrize('prefix', ['Example ``` literal\n\n', 'Example ```` literal\n\n'])
def test_unmatched_inline_run_cannot_borrow_a_fence_opener(prefix):
    marker = '````' if '````' in prefix else '```'
    text = prefix + marker + 'text\npayload\n' + marker + '\n'
    spans = markdown_literal_spans(text)
    assert all(left[1] <= right[0] for left, right in zip(spans, spans[1:]))
    assert spans == [(len(prefix), len(text))]
    projected = visible_mixed_html_text(text)
    assert projected.count(marker + 'text') == 1
    assert projected.count('payload') == 1
    case = {'url': 'https://example.test/', 'required_patterns': [
        {'id': 'code', 'match_scope': 'code_compact', 'pattern': '^payload$'}]}
    assert evaluate_response(case, text.encode())['passed']


@pytest.mark.parametrize('label,suffix,expected', [('Stack Overflow', 'has answers', 'Stack Overflow has answers'),
    ('/docs/start', 'contains text', '/docs/start contains text'), ('read', 'able', 'readable')])
def test_final_suffix_has_same_label_boundary_as_an_intermediate_gap(label, suffix, expected):
    text = '[' + label + '](https://example.test/)' + suffix
    assert visible_markdown_links(text) == expected
    assert visible_markdown_links(text + ' [More](https://example.test/more)').startswith(expected)


@pytest.mark.parametrize('later', [False, True])
def test_required_multiword_fact_does_not_depend_on_a_later_unrelated_link(later):
    text = '[Stack Overflow](https://example.test/)has answers'
    if later:
        text += ' [More](https://example.test/more)'
    assert evaluate_response({'url': 'https://example.test/', 'required_facts': [
        {'id': 'site', 'alternatives': ['Stack Overflow']}]}, text.encode())['passed']


def test_single_word_link_suffix_does_not_invent_a_complete_word():
    assert evaluate_response({'url': 'https://example.test/', 'required_facts': [
        {'id': 'word', 'alternatives': ['read']}]}, b'[read](https://example.test/)able')['passed'] is False
