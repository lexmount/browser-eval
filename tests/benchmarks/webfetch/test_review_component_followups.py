"""Explicit component boundaries and observed literal labels retain ownership."""
from html import escape

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from .test_review_component_representation import COMMENT, POST, _component, _title


BOUNDARIES = []
for index, target in enumerate(['https://other.test/p/2', 'https://user@example.test/p/2',
                                'https://example.test:invalid/p/2', 'https://[invalid/p/2']):
    for first_body, passed in [('', False), ('Owned first body', True)]:
        BOUNDARIES.append((f'boundary-{index}-{passed}',
                           'Alice\nhttps://example.test/p/1\n' + first_body + '\n\nBob\n' + target + '\nUnowned body',
                           'plain_text', _component('post', POST), passed))

HTML_TITLES = [
    ('html-h1', '<h1>Guide</h1>', 'html', _title('Guide'), True),
    ('html-span', '<span>Guide</span>', 'html', _title('Guide'), False),
    ('html-wrapper', '<article><span>Guide</span></article>', 'html', _title('Guide'), False),
    ('html-markdown-text', '# Guide', 'html', _title('Guide'), False),
    ('html-text', 'Guide', 'html', _title('Guide'), False),
    ('html-nested-title', '<article><h1>Guide</h1></article>', 'html', _title('Guide'), True),
    ('html-literal-heading', '<h1># Guide</h1>', 'html', _title('# Guide'), True),
    ('html-wrong-heading', '<h1># Guide</h1>', 'html', _title('Guide'), False),
    ('legacy-title', 'Guide', None, _title('Guide'), True),
    ('markdown-title', '# Guide', 'markdown', _title('Guide'), True),
]

LABELS = []
for component, base in [('comment', COMMENT), ('post', POST)]:
    for index, (representation, ignored, body, passed) in enumerate([
        ('plain_text', '**Reply**', '**Reply**', False),
        ('plain_text', 'Reply', '**Reply**', True),
        ('markdown', '**Reply**', '**Reply**', True),
        ('markdown', 'Reply', '**Reply**', False),
        ('markdown', '**Reply**', '`**Reply**`', False),
        ('markdown', 'Reply', '`**Reply**`', True),
        ('markdown', '**Reply**', r'\*\*Reply\*\*', False),
        ('markdown', 'Reply', r'\*\*Reply\*\*', True),
        ('html', '**Reply**', '**Reply**', False),
        ('html', 'Reply', '**Reply**', True),
    ]):
        contract = dict(base, ignored_labels=[ignored])
        if representation == 'html':
            if component == 'comment':
                text = '<div class="comment"><span class="author">Alice</span><div class="text">' + escape(body) + '</div></div>'
            else:
                text = '<div class="post"><a class="permalink" href="/p/1">Post</a><span class="author">Alice</span><div class="body">' + escape(body) + '</div></div>'
        elif component == 'comment':
            text = '## Comments\n\nAlice: ' + body
        else:
            header = 'Alice\nhttps://example.test/p/1' if representation == 'plain_text' else '## [Alice](/p/1)'
            text = header + '\n' + body
        LABELS.append((f'label-{component}-{index}', text, representation, _component(component, contract), passed))
for ignored, passed in [('**Bot**', False), ('Bot', True)]:
    LABELS.append((f'plain-non-author-{passed}', '**Bot**\nhttps://example.test/p/1\nReal body',
                   'plain_text', _component('post', dict(POST, non_author_labels=[ignored])), passed))
LABELS.extend([
    ('literal-title-format', '# **Guide**', 'markdown', _title('**Guide**'), False),
    ('literal-title-code', '# `**Guide**`', 'markdown', _title('**Guide**'), True),
    ('literal-title-code-wrong', '# `**Guide**`', 'markdown', _title('Guide'), False),
    ('literal-title-escape', r'# \*\*Guide\*\*', 'markdown', _title('**Guide**'), True),
])


@pytest.mark.parametrize('identifier,text,representation,requirements,passed', BOUNDARIES + HTML_TITLES + LABELS)
def test_explicit_components_keep_boundaries_and_literal_contract_values(identifier, text, representation, requirements, passed):
    metadata = {} if representation is None else {'answer_format': representation}
    result = evaluate_response({'url': 'https://example.test/', **requirements}, text.encode(), metadata=metadata)
    assert result['passed'] is passed


def scoring_controls(cases, root_cause):
    for identifier, text, representation, requirements, passed in cases:
        yield {'id': 'runtime8-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if passed else 'false-positive', 'root_cause': root_cause,
               'case': {'url': 'https://example.test/', **requirements}, 'artifact': text,
               'metadata': {} if representation is None else {'answer_format': representation},
               'ok': True, 'expected_task_pass': passed}
