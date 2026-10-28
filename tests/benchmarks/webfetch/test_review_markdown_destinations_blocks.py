"""Rendered link identities and literal-block ownership stay representation-aware."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.reference_quality import markdown_links, markdown_literal_spans
from .test_review_component_representation import POST


LINK = '[Example](https://allowed.example/item)'
BLOCK_CASES = [
    ('spaces', '    ' + LINK, False),
    ('tab', '\t' + LINK, False),
    ('space-tab', ' \t' + LINK, False),
    ('after-paragraph', 'Intro\n\n    ' + LINK, False),
    ('after-heading', '# Heading\n    ' + LINK, False),
    ('after-fence', '```text\nExample\n```\n    ' + LINK, False),
    ('paragraph-continuation', 'Intro\n    ' + LINK, True),
    ('tab-paragraph-continuation', 'Intro\n\t' + LINK, True),
    ('normal-link', LINK, True),
    ('bulleted-link', '- ' + LINK, True),
    ('ordered-link', '1. ' + LINK, True),
    ('list-continuation', '- Intro\n    ' + LINK, True),
    ('loose-list-paragraph', '- Intro\n\n    ' + LINK, True),
    ('ordered-list-paragraph', '1. Intro\n\n    ' + LINK, True),
    ('ordered-list-second-item', '1. Intro\n2. Next\n\n    ' + LINK, True),
    ('list-code', '- Intro\n\n      ' + LINK, False),
    ('ordered-list-code', '1. Intro\n\n       ' + LINK, False),
    ('nested-list-paragraph', '- Outer\n  - Inner\n\n      ' + LINK, True),
    ('nested-list-code', '- Outer\n  - Inner\n\n        ' + LINK, False),
    ('list-inline-code-block', '-     ' + LINK, False),
    ('empty-list-continuation', '-\n    ' + LINK, True),
    ('empty-list-ended', '-\n\n    ' + LINK, False),
    ('quote-code', '>     ' + LINK, False),
    ('quote-paragraph-continuation', '> Intro\n>     ' + LINK, True),
    ('quote-code-after-blank', '> Intro\n>\n>     ' + LINK, False),
    ('short-hyphens-paragraph', '--\n    ' + LINK, True),
]


def named_case(path='/item'):
    return {'url': 'https://allowed.example/', 'acceptance_contract': {
        'version': 1, 'schema': 'named_link', 'minimum_records': 1,
        'hosts': ['allowed.example'], 'entry_path': '^' + re.escape(path) + '$',
        'stable_text_anchors': []}}


@pytest.mark.parametrize('identifier,text,expected', BLOCK_CASES)
def test_indentation_keeps_code_separate_from_paragraphs_and_lists(identifier, text, expected):
    result = evaluate_response(named_case(), text.encode(), metadata={'answer_format': 'markdown'})
    assert result['measured']
    assert result['passed'] is expected
    position = text.index(LINK)
    assert any(start <= position < end for start, end in markdown_literal_spans(text)) is (not expected)


DESTINATIONS = [
    ('escaped-parentheses', r'/item\(one\)', '/item(one)', True),
    ('encoded-parentheses', '/item%28one%29', '/item(one)', True),
    ('literal-parentheses', '/item(one)', '/item(one)', True),
    ('escaped-entity', r'/item\&amp;', '/item&amp;', True),
    ('escaped-entity-not-ampersand', r'/item\&amp;', '/item&', False),
    ('nested-entity', '/item&amp;amp;', '/item&amp;', True),
    ('entity', '/item&amp;', '/item&', True),
    ('encoded-slash-kept', '/item%2Fone', '/item%2Fone', True),
    ('encoded-slash-not-separator', '/item%2Fone', '/item/one', False),
    ('literal-slash', '/item/one', '/item/one', True),
]


@pytest.mark.parametrize('identifier,destination,path,expected', DESTINATIONS)
def test_markdown_destination_uses_one_source_decode(identifier, destination, path, expected):
    text = '[Example](https://allowed.example' + destination + ')'
    result = evaluate_response(named_case(path), text.encode(), metadata={'answer_format': 'markdown'})
    assert result['measured']
    assert result['passed'] is expected


@pytest.mark.parametrize('destination', ['/item(one)', '/item%28one%29'])
def test_equivalent_native_html_parentheses_follow_the_existing_identity_policy(destination):
    text = '<a href="https://allowed.example' + destination + '">Example</a>'
    assert evaluate_response(named_case('/item(one)'), text.encode(), metadata={'answer_format': 'html'})['passed']


@pytest.mark.parametrize('minimum,expected', [(1, True), (2, False)])
def test_equivalent_html_and_escaped_markdown_links_are_one_identity(minimum, expected):
    case = named_case('/item(one)')
    case['acceptance_contract']['minimum_records'] = minimum
    text = (r'[Example](https://allowed.example/item\(one\))' + '\n'
            '<a href="https://allowed.example/item%28one%29">Example</a>')
    result = evaluate_response(case, text.encode(), metadata={'answer_format': 'markdown'})
    assert result['passed'] is expected
    assert result['metrics']['stable_complete_record_count'] == 1


@pytest.mark.parametrize('source,expected', [
    (r'/item\&amp;', '/item&amp;'),
    ('/item&amp;amp;', '/item&amp;'),
    (r'/item\&\#35;', '/item&#35;'),
    (r'/item\\&amp;', '/item\\&'),
    (r'/item\a', '/item\\a'),
    ('/item&amp=one', '/item&amp=one'),
    ('/item&notanentity;', '/item&notanentity;'),
    ('/item&#0000000065;', '/item&#0000000065;'),
])
def test_destination_and_optional_title_decode_without_rescanning(source, expected):
    parsed = markdown_links('[Example](' + source + ' "' + source + '")')[0]
    assert parsed.destination == expected
    assert parsed.title == expected


def test_indented_native_html_remains_a_real_link():
    text = '<article>\n\n    <a href="https://allowed.example/item">Example</a>\n</article>'
    assert evaluate_response(named_case(), text.encode(), metadata={'answer_format': 'html'})['passed']


@pytest.mark.parametrize('representation', ['markdown', 'html', 'plain_text'])
@pytest.mark.parametrize('expected', [True, False])
def test_post_permalink_retains_terminal_reserved_semicolon(representation, expected):
    contract = {**POST, 'entry_path': r'/p/1;' if expected else r'/p/1'}
    text = {
        'markdown': '## Alice [Today](/p/1;)\nReal post text.',
        'html': '<div class="post"><span class="author">Alice</span><a class="permalink" href="/p/1;">Today</a><div class="body">Real post text.</div></div>',
        'plain_text': 'Alice\nhttps://example.test/p/1;\nReal post text.',
    }[representation]
    result = evaluate_response({'url': 'https://example.test/', 'source_benchmark': {'post_contract': contract}},
                               text.encode(), metadata={'answer_format': representation})
    assert result['measured']
    assert result['passed'] is expected


@pytest.mark.parametrize('source,expected', [
    ('    A\n    B\n', 'A\nB\n'),
    ('        A\n', '    A\n'),
    ('- item\n\n      A\n', 'A\n'),
    ('>     A\n', 'A\n'),
    ('-     A\n', 'A\n'),
    ('    A\n      \n    B\n\n', 'A\n  \nB\n'),
])
def test_displayed_code_removes_container_columns_preserving_payload_indentation(source, expected):
    from lexbench_webfetch.reference_quality import markdown_indented_code_contents
    assert list(markdown_indented_code_contents(source).values()) == [expected]


def scoring_controls():
    chosen = {'spaces', 'tab', 'paragraph-continuation', 'loose-list-paragraph', 'list-code'}
    for identifier, text, expected in BLOCK_CASES:
        if identifier in chosen:
            yield {'id': 'markdown-indented-' + identifier, 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Only literal blocks exclude links; paragraph and list indentation retain their visible links.',
                   'case': named_case(), 'artifact': text, 'metadata': {'answer_format': 'markdown'},
                   'ok': True, 'expected_task_pass': expected}
    chosen_destinations = {'escaped-parentheses', 'escaped-entity', 'escaped-entity-not-ampersand',
                           'encoded-slash-kept', 'encoded-slash-not-separator'}
    for identifier, destination, path, expected in DESTINATIONS:
        if identifier in chosen_destinations:
            yield {'id': 'markdown-destination-' + identifier, 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Decode Markdown escapes and references once without decoding percent-encoded reserved URL delimiters.',
                   'case': named_case(path),
                   'artifact': '[Example](https://allowed.example' + destination + ')',
                   'metadata': {'answer_format': 'markdown'}, 'ok': True, 'expected_task_pass': expected}
    for expected in [True, False]:
        yield {'id': 'post-terminal-semicolon-' + ('retained' if expected else 'distinct'),
               'category': 'answer-surface', 'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Joining answer URLs must retain terminal reserved path punctuation.',
               'case': {'url': 'https://example.test/', 'source_benchmark': {
                   'post_contract': {**POST, 'entry_path': r'/p/1;' if expected else r'/p/1'}}},
               'artifact': '## Alice [Today](/p/1;)\nReal post text.',
               'metadata': {'answer_format': 'markdown'}, 'ok': True, 'expected_task_pass': expected}
