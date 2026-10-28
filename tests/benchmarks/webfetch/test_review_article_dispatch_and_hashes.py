"""Declared HTML selects native fields; Markdown closers require an opener."""
from copy import deepcopy

import pytest

from lexbench_webfetch.evaluation import evaluate_response

from .test_review_component_representation import POST, COMMENT


def controls():
    for component, contract in (('post', POST), ('comment', COMMENT)):
        for prefix in ('', '<span>Feed</span>', 'Feed text'):
            for complete in (False, True):
                body = 'Actual visible prose.' if complete else ''
                if component == 'post':
                    content = ('<article class="post"><span class="author">Alice</span>'
                               '<a class="permalink" href="/p/1">Today</a><span class="body">'
                               + body + '</span></article>')
                else:
                    content = ('<div class="comment"><span class="author">Alice</span>'
                               '<span class="text">' + body + '</span></div>')
                name = 'inline' if prefix.startswith('<span') else 'text' if prefix else 'none'
                yield (f'{component}-html-prefix-{name}-' + ('complete' if complete else 'empty'),
                       prefix + content, {'source_benchmark': {component + '_contract': contract}}, 'html', complete)
        html = ('<article class="post"><span class="author">Alice</span><a class="permalink" href="/p/1">Today</a>'
                '<span class="body">Actual prose.</span></article>' if component == 'post' else
                '<div class="comment"><span class="author">Alice</span><span class="text">Actual prose.</span></div>')
        yield (f'{component}-plain-html-is-literal', html,
               {'source_benchmark': {component + '_contract': contract}}, 'plain_text', False)
    for text, representation, expected_title, passed, name in (
        ('Release ###', 'markdown', 'Release ###', True, 'literal-preserved'),
        ('Release ###', 'markdown', 'Release', False, 'literal-not-truncated'),
        ('## Release ##', 'markdown', 'Release', True, 'heading-closers'),
        ('## Release ##', 'markdown', 'Release ##', False, 'heading-not-literal'),
        ('Release ###', 'plain_text', 'Release ###', True, 'plain-preserved'),
        ('Release ###', 'plain_text', 'Release', False, 'plain-not-truncated'),
        ('<h1>Release ###</h1>', 'html', 'Release ###', True, 'html-preserved'),
        ('<h1>Release ###</h1>', 'html', 'Release', False, 'html-not-truncated'),
    ):
        contract = {'version': 1, 'alternatives': [expected_title], 'html_selector': 'h1'}
        yield ('title-hashes-' + name, text, {'source_benchmark': {'title_contract': contract}}, representation, passed)


@pytest.mark.parametrize('identifier, text, requirements, representation, expected', list(controls()))
def test_native_dispatch_and_literal_title_suffix(identifier, text, requirements, representation, expected):
    result = evaluate_response({'url': 'https://example.test/', **deepcopy(requirements)}, text.encode(),
                               metadata={'answer_format': representation})
    assert result['measured']
    assert result['passed'] is expected


def scoring_controls():
    selected = {
        'post-html-prefix-inline-complete', 'post-html-prefix-inline-empty',
        'comment-html-prefix-inline-complete', 'comment-html-prefix-inline-empty',
        'title-hashes-literal-preserved', 'title-hashes-literal-not-truncated',
        'title-hashes-heading-closers', 'title-hashes-heading-not-literal',
    }
    for identifier, text, requirements, representation, expected in controls():
        if identifier in selected:
            yield {'id': identifier, 'failure_mode': 'false-negative' if expected else 'false-positive',
                   'category': 'answer-surface',
                   'root_cause': 'Article components must honor declared HTML and preserve literal hashes outside actual Markdown heading syntax.',
                   'case': {'url': 'https://example.test/', **requirements}, 'artifact': text,
                   'metadata': {'answer_format': representation}, 'ok': True, 'expected_task_pass': expected}
