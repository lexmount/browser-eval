"""Only title lines outside quoted and list paragraphs establish a title."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response


CASES = [
    ('quote-lazy', '> An excerpt:\nWhy We Read', False),
    ('quote-multiple-lazy', '> An excerpt:\ncontinued text\nWhy We Read', False),
    ('quote-blank', '> An excerpt:\n\nWhy We Read', True),
    ('quote-outside-heading', '> An excerpt:\n# Why We Read', True),
    ('quote-heading-block', '> ## A heading\nWhy We Read', True),
    ('quote-thematic-block', '> ---\nWhy We Read', True),
    ('quote-empty', '>\nWhy We Read', True),
    ('quote-empty-line', '> An excerpt:\n>\nWhy We Read', True),
    ('quote-nested', '> > An excerpt:\nWhy We Read', False),
    ('quote-list-lazy', '> - An item\nWhy We Read', False),
    ('quote-empty-list', '> -\nWhy We Read', True),
    ('quote-setext-lazy', '> An excerpt:\n===\nWhy We Read', False),
    ('quote-thematic-interruption', '> An excerpt:\n---\nWhy We Read', True),
    ('quote-indented-lazy', '> An excerpt:\n    continued text\nWhy We Read', False),
    ('quote-indented-code', '>     Literal code\nWhy We Read', True),
    ('quote-code-block', '> ```\n> Literal code\n> ```\nWhy We Read', True),
    ('quote-unclosed-tilde-block', '> ~~~\n> Literal code\nWhy We Read', True),
    ('quote-html-blank', '> An <em>excerpt</em>:\n\nWhy We Read', True),
    ('quote-html-lazy', '> An <em>excerpt</em>:\nWhy We Read', False),
    ('list-lazy', '- A bullet\nWhy We Read', False),
    ('ordered-list-lazy', '1. A bullet\nWhy We Read', False),
    ('list-blank', '- A bullet\n\nWhy We Read', True),
    ('list-outside-heading', '- A bullet\n# Why We Read', True),
    ('list-empty', '-\nWhy We Read', True),
    ('list-indented-paragraph', '- A bullet\n\n  Why We Read', False),
    ('list-owned-heading', '- A bullet\n  # Why We Read', False),
    ('list-owned-code-then-paragraph', '- A bullet\n  ```\n  Literal code\n  ```\n  Why We Read', False),
    ('list-owned-quote-then-heading', '- A bullet\n  > An excerpt\n  # Why We Read', False),
    ('nested-list-then-parent-paragraph', '- A bullet\n  - A nested bullet\n\n  Why We Read', False),
    ('code-literal-quote', '```text\n> An excerpt\n```\n\nWhy We Read', True),
    ('inline-literal-quote', '`\n> An excerpt\nLiteral`\n\nWhy We Read', True),
]


@pytest.mark.parametrize('identifier,text,expected', CASES)
def test_title_lines_obey_markdown_container_paragraphs(identifier, text, expected):
    contract = {'version': 1, 'alternatives': ['Why We Read'], 'html_selector': 'h1'}
    result = evaluate_response({'url': 'https://example.test/', 'source_benchmark': {'title_contract': contract}},
                               text.encode(), metadata={'answer_format': 'markdown'})
    assert result['measured']
    assert result['passed'] is expected


def test_plain_text_does_not_inherit_markdown_quote_ownership():
    contract = {'version': 1, 'alternatives': ['Why We Read'], 'html_selector': 'h1'}
    result = evaluate_response({'url': 'https://example.test/', 'source_benchmark': {'title_contract': contract}},
                               b'> An excerpt:\nWhy We Read', metadata={'answer_format': 'plain_text'})
    assert result['passed']


def scoring_controls():
    selected = {'quote-lazy', 'quote-blank', 'quote-outside-heading', 'quote-heading-block',
                'quote-code-block', 'list-lazy', 'list-blank', 'list-outside-heading'}
    for identifier, text, expected in CASES:
        if identifier in selected:
            yield {'id': 'title-paragraph-' + identifier,
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'category': 'answer-surface',
                   'root_cause': 'Lazy continuation of a quoted or list paragraph cannot establish a standalone article title.',
                   'case': {'url': 'https://example.test/', 'source_benchmark': {'title_contract': {
                       'version': 1, 'alternatives': ['Why We Read'], 'html_selector': 'h1'}}},
                   'artifact': text, 'metadata': {'answer_format': 'markdown'},
                   'ok': True, 'expected_task_pass': expected}
