"""Declared Markdown metadata cannot stand in for returned page content."""
import hashlib

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


ANCHOR_CASE = {
    'url': 'https://example.test/page', 'min_content_chars': 1,
    'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                            'stable_text_anchors': [{'id': 'identity', 'alternatives': ['Page Label']}]},
}
HEADER = '---\ntitle: "Other title"\nmeta:\n  "og:title": "Page Label"\n  "twitter:title": "Page Label"\n---\n'


@pytest.mark.parametrize('header', [HEADER, HEADER.replace('\n', '\r\n'), '\ufeff'+HEADER,
                                    HEADER.replace('\n---\n', '\n...\n')])
@pytest.mark.parametrize('body,expected', [('Ordinary text', False), ('# Page Label\nOrdinary text', True)])
def test_body_anchor_ignores_front_matter(header, body, expected):
    answer = (header+body).encode()
    metrics = score_attempt(ANCHOR_CASE, answer, ok=True)
    assert task_passed(True, metrics) is expected
    assert metrics['stable_anchor_hits'] == int(expected)
    assert metrics['content_sha256'] == hashlib.sha256(answer).hexdigest()
    assert metrics['content_chars'] == len(answer.decode())


@pytest.mark.parametrize('answer', [
    '---\nPage Label\n---\nOrdinary text',
    '---\nPage Label\nfield: value\n---\nOrdinary text',
    '---\ntitle: Page Label\nOrdinary text',
    '```yaml\n---\ntitle: Page Label\n---\n```\nOrdinary text',
    'Paragraph\n---\ntitle: Page Label\n---\n',
])
def test_ordinary_rules_unclosed_headers_and_examples_remain_body(answer):
    metrics = score_attempt(ANCHOR_CASE, answer.encode(), ok=True)
    assert task_passed(True, metrics)


@pytest.mark.parametrize('body,expected', [('Ordinary text', False), ('Page Label is visible article prose.', True)])
def test_body_coverage_does_not_borrow_metadata(body, expected):
    case = {'url': 'https://example.test/page', 'min_content_chars': 1,
            'body_requirements': {'version': 3, 'unit': 'region_compact_hexagram_recall',
                                  'minimum_coverage': .8, 'minimum_floor': .6,
                                  'minimum_region_passes': 1,
                                  'regions': [{'id': 'body', 'text': 'Page Label is visible article prose.'}]}}
    metrics = score_attempt(case, (HEADER+body).encode(), ok=True)
    assert task_passed(True, metrics) is expected
    assert metrics['body_coverage_pass'] is expected


def test_front_matter_can_precede_a_visible_html_body():
    metrics = score_attempt(ANCHOR_CASE, (HEADER+'<main><h1>Page Label</h1></main>').encode(), ok=True)
    assert task_passed(True, metrics)


def test_visible_html_metadata_example_remains_actual_body():
    answer = '<main><p>---</p><p>title: Page Label</p><p>---</p></main>'
    metrics = score_attempt(ANCHOR_CASE, answer.encode(), ok=True)
    assert task_passed(True, metrics)
