"""References, code-block identities and inline title text keep their boundaries."""
from copy import deepcopy

import pytest

from lexbench_webfetch.evaluation import evaluate_response

from .test_review_component_representation import POST, COMMENT


def controls():
    for path in ('/doc', '/other/2', '/p/2'):
        for separated in (False, True):
            for original in (False, True):
                body = 'Actual first body.' if original else ''
                prefix = 'Alice\nhttps://example.test/p/1' + ('\n' + body if body else '')
                text = prefix + ('\n\n' if separated else '\n') + 'Useful reference\nhttps://external.test' + path
                boundary = path == '/p/2' and separated
                yield (f'plain-post-reference-{path[1:].replace("/", "-")}-'
                       f'{"paragraph" if separated else "inline"}-{ "original" if original else "empty"}',
                       text, {'source_benchmark': {'post_contract': POST}}, 'plain_text', original or not boundary)
    for separated in (False, True):
        text = ('Alice\nhttps://example.test/p/1' + ('\n\n' if separated else '\n')
                + 'Useful reference\nhttps://example.test/other/2')
        yield ('plain-post-same-host-reference-' + ('paragraph' if separated else 'inline'),
               text, {'source_benchmark': {'post_contract': POST}}, 'plain_text', True)
    for size in (11, 21):
        for last in ('empty', 'duplicate', 'unique'):
            blocks = []
            for index in range(size):
                code = ('' if last == 'empty' else 'Literal body 1.' if last == 'duplicate' else 'Distinct last body.') if index == size-1 else f'Literal body {index}.'
                blocks.append('### Alice\n\n```text\n' + code + '\n```')
            contract = {**COMMENT, 'minimum_comments': size}
            yield (f'retained-code-{size}-{last}', '## Comments\n' + '\n\n'.join(blocks),
                   {'source_benchmark': {'comment_contract': contract}}, 'markdown', last == 'unique')
    for name, expected, title in (
        ('split-word', 'Reading', 'Read<em>ing</em>'),
        ('cjk', '浏览器评测', '<span>浏览器</span><span>评测</span>'),
        ('spacing', 'Read ing', 'Read <em>ing</em>'),
        ('block-boundary', 'Read ing', '<div>Read</div><div>ing</div>'),
    ):
        for matches in (False, True):
            contract = {'version': 1, 'alternatives': [expected if matches else expected + ' extra'],
                        'html_selector': 'h1'}
            yield (f'html-title-adjacency-{name}-' + ('match' if matches else 'wrong'),
                   '<h1>' + title + '</h1>', {'source_benchmark': {'title_contract': contract}}, 'html', matches)


@pytest.mark.parametrize('identifier, text, requirements, representation, expected', list(controls()))
def test_article_boundary_fidelity(identifier, text, requirements, representation, expected):
    result = evaluate_response({'url': 'https://example.test/', **deepcopy(requirements)}, text.encode(),
                               metadata={'answer_format': representation})
    assert result['measured']
    assert result['passed'] is expected


def scoring_controls():
    selected = {
        'plain-post-reference-doc-inline-empty', 'plain-post-reference-p-2-paragraph-empty',
        'plain-post-reference-p-2-paragraph-original',
        'retained-code-11-empty', 'retained-code-11-unique',
        'html-title-adjacency-split-word-match', 'html-title-adjacency-split-word-wrong',
        'html-title-adjacency-cjk-match',
    }
    for identifier, text, requirements, representation, expected in controls():
        if identifier in selected:
            yield {'id': identifier, 'failure_mode': 'false-negative' if expected else 'false-positive',
                   'category': 'answer-surface',
                   'root_cause': 'Article parsing must retain ordinary reference prose, exact code-block markers and rendered inline title adjacency.',
                   'case': {'url': 'https://example.test/', **requirements}, 'artifact': text,
                   'metadata': {'answer_format': representation}, 'ok': True, 'expected_task_pass': expected}
