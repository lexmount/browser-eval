"""Linked table aliases match displayed headers with literal punctuation intact."""

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


def case(alias):
    return composite_case({'id': 'items', 'kind': 'linked_table', 'minimum_records': 1,
        'hosts': ['example.test'], 'entry_path': r'/item/\d+', 'link_column': 'name', 'identity_column': 'name',
        'required_columns': [{'id': 'name', 'kind': 'text', 'aliases': [alias]},
                             {'id': 'owner', 'kind': 'text', 'aliases': ['Owner']}]})


SCENARIOS = [
    ('parentheses', 'Item name (primary)', r'Item name \(primary\)', 'Item name (primary)', True),
    ('wrong-parentheses', 'Item name (secondary)', r'Item name \(secondary\)', 'Item name (primary)', False),
    ('literal-stars', '*Item*', r'\*Item\*', '*Item*', True),
    ('literal-stars-distinct', '*Item*', r'\*Item\*', 'Item', False),
    ('formatting', '<strong>Item</strong>', '**Item**', 'Item', True),
    ('formatting-distinct', '<strong>Item</strong>', '**Item**', '**Item**', False),
    ('literal-code', '`Item`', r'\`Item\`', '`Item`', True),
    ('code-formatting', '<code>Item</code>', '`Item`', 'Item', True),
    ('linked-header', '<a href="/docs">Item name (primary)</a>', '[Item name (primary)](/docs)', 'Item name (primary)', True),
    ('literal-link', '[Item](/docs)', r'\[Item\]\(/docs\)', '[Item](/docs)', True),
]


def artifact(shape, native, markdown):
    if shape == 'html':
        return '<table><tr><th>' + native + '</th><th>Owner</th></tr><tr><td><a href="/item/1">Alpha</a></td><td>Alice</td></tr></table>'
    if shape == 'pipe':
        return '|'+markdown+'|Owner|\n|---|---|\n|[Alpha](/item/1)|Alice|'
    return markdown+'\nOwner\n[Alpha](/item/1)\n[Alice](/people/alice)'


@pytest.mark.parametrize('identifier,native,markdown,alias,expected', SCENARIOS)
@pytest.mark.parametrize('shape', ['html', 'pipe', 'linearized'])
def test_linked_headers_share_visible_scalar_alias_semantics(identifier, native, markdown, alias, expected, shape):
    metrics = score_attempt(case(alias), artifact(shape,native,markdown).encode(), ok=True)
    assert task_passed(True, metrics, 'body') is expected
