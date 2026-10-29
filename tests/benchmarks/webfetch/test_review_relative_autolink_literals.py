"""Relative autolink projection never edits visible code payloads."""
import re

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.reference_quality import visible_markdown_links
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_literal_pattern_surface import contract


SHAPES = [
    ('inline', '`</docs/start>`'),
    ('double-inline', '``</docs/start>``'),
    ('fenced', '```text\n</docs/start>\n```'),
    ('tilde-fenced', '~~~text\n</docs/start>\n~~~'),
    ('indented', '    </docs/start>'),
    ('tab-indented', '\t</docs/start>'),
    ('link-label-code', '[`</docs/start>`](/other)'),
]


@pytest.mark.parametrize('identifier,source', SHAPES)
@pytest.mark.parametrize('angles', [False,True])
def test_page_pattern_sees_literal_angles_in_code(identifier,source,angles):
    expected='</docs/start>' if angles else '/docs/start'
    case={'url':'https://example.test/','acceptance_contract':contract('^'+re.escape(expected)+'$')}
    metrics=score_attempt(case,source.encode(),ok=True)
    assert task_passed(True,metrics,'body') is angles


@pytest.mark.parametrize('identifier,source',SHAPES[:-1])
def test_visible_markdown_links_preserves_original_code_spelling(identifier,source):
    assert visible_markdown_links(source) == source


@pytest.mark.parametrize('angles',[False,True])
def test_actual_relative_autolink_still_exposes_its_visible_path(angles):
    expected='</docs/start>' if angles else '/docs/start'
    case={'url':'https://example.test/','acceptance_contract':contract('^'+re.escape(expected)+'$')}
    metrics=score_attempt(case,b'</docs/start>',ok=True)
    assert task_passed(True,metrics,'body') is (not angles)


def test_prior_link_projection_does_not_shift_code_ownership():
    source='[Introduction](/overview)\n`</docs/start>`\n</docs/actual>'
    assert visible_markdown_links(source) == 'Introduction\n`</docs/start>`\n/docs/actual'
