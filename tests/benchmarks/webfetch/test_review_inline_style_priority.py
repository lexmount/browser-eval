"""Inline CSS priority changes visibility consistently across answer surfaces."""
import pytest

from lexbench_webfetch.article_title import verify_article_title
from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task, task


STYLES = [
    ('display:bogus!important;display:none', False),
    ('visibility:bogus!important;visibility:hidden', False),
    ('opacity:bogus!important;opacity:0', False),
    ('display:block!important;display:bogus!important', True),
    ('visibility:visible!important;visibility:bogus!important', True),
    ('opacity:1!important;opacity:bogus!important', True),
    ('display:!important;display:none', False),
    ('opacity:-1!important;opacity:1', False),
    ('opacity:0e0!important;opacity:1', False),
    ('opacity:0.5!important;opacity:0', True),
    ('display:inline flex!important;display:none', True),
    ('display:none !IMPORTANT', False),
    ('DISPLAY: none ! Important', False),
    ('display:none!important;display:block', False),
    ('display:block;display:none!important', False),
    ('display:none!important;display:block!important', True),
    ('display:block!important;display:none!important', False),
    ('display:none;display:block!important', True),
    ('display:block!important;display:none', True),
    ('display:none;display:block', True),
    ('visibility:hidden !IMPORTANT;visibility:visible', False),
    ('visibility:hidden!important;visibility:visible!important', True),
    ('visibility:visible !important;visibility:collapse', True),
    ('visibility:collapse !important;visibility:visible', False),
    ('opacity:0 !IMPORTANT;opacity:1', False),
    ('opacity:0!important;opacity:1!important', True),
    ('opacity:1 !important;opacity:0', True),
    ('display:none !/**/IMPORTANT;display:block', False),
    ('display:block !important;visibility:hidden', False),
]


@pytest.mark.parametrize('style,visible', STYLES)
def test_css_priority_is_shared_by_title_records_and_body(style, visible):
    title = f'<h1 class="article-title" style="{style}">Why We Read</h1>'
    assert verify_article_title(title, {'version': 1, 'alternatives': ['Why We Read'],
                                      'html_selector': 'h1.article-title'})['article_title_pass'] is visible
    case = record_task()
    body = f'<article style="{style}"><a href="/item/1">Alpha Widget</a></article>'
    assert evaluate_response(case, body.encode())['passed'] is visible
    case = task(required_facts=[{'id': 'fact', 'alternatives': ['Important evidence']}])
    body = f'<section style="{style}">Important evidence</section><p>Ordinary text</p>'
    assert evaluate_response(case, body.encode())['passed'] is visible
