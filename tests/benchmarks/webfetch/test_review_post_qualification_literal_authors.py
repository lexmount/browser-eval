"""Historical authors are extracted scalars, matching runtime author policy."""
from copy import deepcopy
from html import escape

import pytest

from lexbench_webfetch.article_qualification import qualify_article_posts
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.article_contract_fixtures import post_labels


URL = 'https://example.test/feed'
CONTRACT = {**post_labels(), 'version': 2, 'minimum_posts': 1,
            'hosts': ['example.test'], 'entry_path': r'/post/\d+',
            'html_record_selector': 'article.post', 'html_permalink_selector': 'a.time',
            'html_author_selector': '.author', 'html_body_selector': '.body',
            'html_quote_selector': 'blockquote', 'allow_empty_outer_with_quotes': True}
AUTHORS = [('*Today*', True), ('_Today_', True), ('**like**', True), ('_like_', True),
           ('Today', False), ('like', False), ('date', False), ('🙂', True),
           ('', False), ('\u200b', False)]


@pytest.mark.parametrize('author,expected', AUTHORS)
@pytest.mark.parametrize('role,representation', [('outer', 'html'), ('outer', 'plain_text'),
                                               ('quote', 'html'), ('empty-outer-quote', 'html')])
def test_qualification_and_runtime_keep_literal_author_symbols(author, expected, role, representation):
    outer_author = author if role == 'outer' else 'Alice'
    outer_text = '' if role == 'empty-outer-quote' else 'Complete visible outer post prose.'
    parts = [{'kind': 'post', 'text': outer_text, 'complete_initial_text': True}]
    if role != 'outer':
        parts.append({'kind': 'quote', 'author': author,
                      'text': 'Complete visible attributed quote prose.', 'complete_initial_text': True})
    if not outer_text:
        parts[0]['empty_outer_text'] = True
    reference = {'version': 1, 'case_id': 'feed', 'url': URL, 'scope': 'initial_load_only',
                 'context_id': 'historical', 'observed_at': '2026-10-20T00:00:00Z',
                 'source_observation_counts': [1, 1],
                 'records': [{'url': 'https://example.test/post/1', 'author': outer_author,
                              'body_parts': deepcopy(parts)}]}
    if representation == 'plain_text':
        source = outer_author + '\nhttps://example.test/post/1\n' + outer_text
    else:
        quote = ('<blockquote><span class="author">' + escape(author) + '</span><p class="body">Complete visible attributed quote prose.</p></blockquote>'
                 if role != 'outer' else '')
        source = ('<article class="post"><span class="author">' + escape(outer_author)
                  + '</span><a class="time" href="/post/1">Today</a><p class="body">'
                  + outer_text + '</p>' + quote + '</article>')
    case = {'url': URL, 'expected_any': [], 'source_benchmark': {'post_contract': CONTRACT}}
    metrics = score_attempt(case, source.encode(), ok=True, metadata={'answer_format': representation})
    results = metrics['article_post_results']
    if results:
        if role == 'outer':
            assert results[0]['author_pass'] is expected
        else:
            assert results[0]['attributed_quote_pass'] is expected
    else:
        assert not expected
    # Ordinary outer prose can independently satisfy a post even if its quote
    # lacks attribution. Historical evidence still validates every named part.
    assert task_passed(True, metrics) is (True if role == 'quote' else expected)
    status = qualify_article_posts(CONTRACT, reference, case_id='feed', url=URL)['status']
    assert (status == 'qualified') is expected
