"""Literal operands and declared representations survive every acceptance gate."""
import re
from html import escape

import pytest

from lexbench_webfetch.evaluation import evaluate_response


COMMENT = {'version': 2, 'minimum_comments': 1, 'record_selector': '.comment',
           'author_selector': '.author', 'body_selector': '.text', 'section_markers': ['Comments'],
           'ignored_labels': ['Reply'], 'unavailable_notices': []}
POST = {'version': 2, 'minimum_posts': 1, 'hosts': ['example.test'], 'entry_path': r'/p/\d+',
        'html_record_selector': '.post', 'html_permalink_selector': '.permalink',
        'html_author_selector': '.author', 'html_body_selector': '.body',
        'ignored_labels': ['Reply'], 'unavailable_notices': [], 'non_author_labels': []}


def _component(field, contract):
    return {'source_benchmark': {field + '_contract': contract}}


def _title(expected):
    return _component('title', {'version': 1, 'alternatives': [expected], 'html_selector': 'h1'})


def _code(expected):
    return {'required_patterns': [{'id': 'code', 'match_scope': 'code_compact',
                                   'pattern': '^' + re.escape(expected) + '$'}]}


CASES = [
    ('operand', '```python\ntotal = (\n    10 + 20\n)\n```', 'markdown', _code('total=(10+20)'), True),
    ('lost-operand', '```python\ntotal = (\n    10 + 20\n)\n```', 'markdown', _code('total=(+20)'), False),
    ('literal-sequence', '10 + 20\n11 + 30', 'markdown', _code('10+2011+30'), True),
    ('lost-literal-sequence', '10 + 20\n11 + 30', 'markdown', _code('+20+30'), False),
    ('listing', '```python\n01 total = (\n02     10 + 20\n03 )\n```', 'markdown', _code('total=(10+20)'), True),
    ('altered-listing', '```python\n01 total = (\n02     10 + 20\n03 )\n```', 'markdown', _code('total=(+20)'), False),
    ('partial-listing', '05 total = (10 + 20)\n06 print(total)', 'markdown', _code('total=(10+20)print(total)'), True),
    ('broken-sequence', '01 alpha\n03 beta', 'markdown', _code('01alpha03beta'), True),
    ('invented-listing', '01 alpha\n03 beta', 'markdown', _code('alphabeta'), False),
    ('plain-title', 'Guide', 'plain_text', _title('Guide'), True),
    ('plain-link-title', '[Guide](https://example.test/)', 'plain_text', _title('Guide'), False),
    ('markdown-link-title', '[Guide](https://example.test/)', 'markdown', _title('Guide'), True),
    ('literal-link-title', '[Guide](https://example.test/)', 'plain_text', _title('[Guide](https://example.test/)'), True),
    ('plain-html-title', '<h1>Guide</h1>', 'plain_text', _title('Guide'), False),
    ('html-title', '<h1>Guide</h1>', 'html', _title('Guide'), True),
    ('literal-html-title', '<h1>Guide</h1>', 'plain_text', _title('<h1>Guide</h1>'), True),
    ('literal-code-title', '`Guide`', 'plain_text', _title('`Guide`'), True),
    ('wrong-code-title', '`Guide`', 'plain_text', _title('Guide'), False),
    ('plain-comment', 'Comments\n\nAlice: Real comment', 'plain_text', _component('comment', COMMENT), True),
    ('unowned-comment', 'Comments\n\nReal comment', 'plain_text', _component('comment', COMMENT), False),
    ('plain-linked-author', 'Comments\n\n[Alice](https://example.test/u)\nReal comment', 'plain_text', _component('comment', COMMENT), False),
    ('markdown-linked-author', 'Comments\n\n[Alice](https://example.test/u)\nReal comment', 'markdown', _component('comment', COMMENT), True),
    ('plain-post', 'Alice\nhttps://example.test/p/1\nReal post', 'plain_text', _component('post', POST), True),
    ('plain-post-no-author', 'https://example.test/p/1\nReal post', 'plain_text', _component('post', POST), False),
    ('plain-markdown-post', '## [Alice](https://example.test/p/1)\nReal post', 'plain_text', _component('post', POST), False),
    ('markdown-post', '## [Alice](https://example.test/p/1)\nReal post', 'markdown', _component('post', POST), True),
]
for index, body in enumerate(['<span hidden>Literal HTML</span>', '![diagram](image.png)',
                              '**Reply**', '[](https://example.test/)', '```']):
    for representation, expected in [('plain_text', True), ('markdown', False)]:
        comment = '## Comments\n\nAlice:' + ('\n' if body == '```' else ' ') + body
        CASES.append((f'comment-literal-{index}-{representation}', comment,
                      representation, _component('comment', COMMENT), expected))
        header = ('Alice\nhttps://example.test/p/1' if representation == 'plain_text'
                  else '## [Alice](https://example.test/p/1)')
        CASES.append((f'post-literal-{index}-{representation}', header + '\n' + body,
                      representation, _component('post', POST), expected))
    CASES.append((f'html-comment-literal-{index}',
                  '<div class="comment"><span class="author">Alice</span><div class="text">' + escape(body) + '</div></div>',
                  'html', _component('comment', COMMENT), True))
    CASES.append((f'html-post-literal-{index}',
                  '<div class="post"><a class="permalink" href="/p/1">Post</a>'
                  '<span class="author">Alice</span><div class="body">' + escape(body) + '</div></div>',
                  'html', _component('post', POST), True))


@pytest.mark.parametrize('identifier,text,representation,requirements,expected', CASES)
def test_public_acceptance_keeps_literal_operands_and_component_representations(identifier, text, representation, requirements, expected):
    result = evaluate_response({'url': 'https://example.test/', **requirements}, text.encode(),
                               metadata={'answer_format': representation})
    assert result['passed'] is expected


def scoring_controls():
    for identifier, text, representation, requirements, expected in CASES:
        yield {'id': 'runtime8-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': ('coherent_code_line_number_evidence' if 'required_patterns' in requirements
                              else 'declared_representation_at_required_component_boundary'),
               'case': {'url': 'https://example.test/', **requirements}, 'artifact': text,
               'ok': True, 'metadata': {'answer_format': representation}, 'expected_task_pass': expected}
