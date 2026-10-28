"""Answer URL joins preserve empty terminal parameters and literal identities."""
from urllib.parse import urljoin

import pytest

from lexbench_webfetch.response_input import join_answer_url, safe_named_links


@pytest.mark.parametrize('base,reference,expected', [
    ('https://example.test/dir/base;', 'https://example.test/item;', 'https://example.test/item;'),
    ('https://example.test/dir/base;', '/item;', 'https://example.test/item;'),
    ('https://example.test/dir/base;', 'item;', 'https://example.test/dir/item;'),
    ('https://example.test/dir/base;', ';', 'https://example.test/dir/;'),
    ('https://example.test/dir/base;', '..;', 'https://example.test/dir/..;'),
    ('https://example.test/dir/base;', '../item;', 'https://example.test/item;'),
    ('https://example.test/dir/base;', '//other.test/item;', 'https://other.test/item;'),
    ('https://example.test/dir/base;?old=1', '?new=1', 'https://example.test/dir/base;?new=1'),
    ('https://example.test/dir/base;?old=1', '#section', 'https://example.test/dir/base;?old=1#section'),
    ('https://example.test/dir/..;', '?new=1', 'https://example.test/dir/..;?new=1'),
    ('https://example.test/dir/base%3B;', '?new=1', 'https://example.test/dir/base%3B;?new=1'),
    ('https://example.test/dir/base;', '/item%3B;', 'https://example.test/item%3B;'),
    ('https://example.test/', '/item&amp;', 'https://example.test/item&amp;'),
])
def test_only_literal_terminal_semicolon_is_restored(base, reference, expected):
    assert join_answer_url(base, reference) == expected
    assert safe_named_links([{'url': reference, 'title': 'Alpha'}], base) == [(expected, 'Alpha')]


@pytest.mark.parametrize('reference', ['', '?new=1', '#section', '/item', '/item&',
                                       '/item%26', '/item%3B', '/item%2Fchild',
                                       'https://other.test/a%3B', '//other.test/a'])
def test_ordinary_join_behavior_and_encoded_reserved_characters_are_unchanged(reference):
    base = 'https://example.test/dir/base?old=1'
    assert join_answer_url(base, reference) == urljoin(base, reference)
