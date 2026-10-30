"""A rendered HTML view is literal text for subsequent matching."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import task


def pattern_case(pattern):
    return task(required_patterns=[{'id': 'visible', 'pattern': pattern, 'minimum': 1}])


@pytest.mark.parametrize('literal', ['[Required label](/example)', '![Required label](/example)', '**Required label**'])
def test_html_literal_markdown_is_not_rendered_a_second_time(literal):
    body = ('<p>' + literal + '</p>').encode()
    assert not evaluate_response(pattern_case(r'^Required label$'), body, metadata={'answer_format': 'html'})['passed']
    assert evaluate_response(pattern_case('^' + re.escape(literal) + '$'), body, metadata={'answer_format': 'html'})['passed']
    assert evaluate_response(pattern_case(r'^Required label$'), b'<p><b>Required label</b></p>',
                             metadata={'answer_format': 'html'})['passed']


@pytest.mark.parametrize('source,visible', [('&lt;b&gt;Alpha&lt;/b&gt;', '<b>Alpha</b>'), ('&amp;amp;', '&amp;')])
def test_rendered_html_tag_and_entity_text_are_not_decoded_again(source, visible):
    body = ('<p>' + source + '</p>').encode()
    case = pattern_case('^' + re.escape(visible) + '$')
    case['ground_truth_main_content'] = visible
    result = evaluate_response(case, body, metadata={'answer_format': 'html'})
    assert result['passed']
    assert result['metrics']['word_f1'] == 1


def test_word_metrics_include_the_actual_html_literal_destination_text():
    case = task(ground_truth_main_content='Required label example')
    result = evaluate_response(case, b'<p>[Required label](/example)</p>', metadata={'answer_format': 'html'})
    assert result['metrics']['word_f1'] == 1
    case['ground_truth_main_content'] = 'Required label'
    result = evaluate_response(case, b'<p><a href="/example">Required label</a></p>', metadata={'answer_format': 'html'})
    assert result['metrics']['word_f1'] == 1


def test_html_leading_text_is_not_markdown_front_matter():
    case = pattern_case(r'Required: label')
    assert evaluate_response(case, b'---\nRequired: label\n---\n<p>Body</p>', metadata={'answer_format': 'html'})['passed']
