"""Explicit auxiliary scopes preserve linked rows without supplying missing links."""
from copy import deepcopy

import pytest

from lexbench_webfetch.composite_tasks import (
    evaluate_composite_acceptance,
    validate_composite_acceptance,
)


GROUP = {
    'id': 'items',
    'kind': 'linked_table',
    'minimum_records': 2,
    'hosts': ['example.test'],
    'entry_path': '/item',
    'identity_query_keys': ['id'],
    'identity_column': 'name',
    'link_column': 'name',
    'required_columns': [
        {'id': 'name', 'kind': 'text', 'aliases': ['Name']},
        {'id': 'author', 'kind': 'text', 'aliases': ['Author']},
    ],
    'table_scope': {'exclude_selector': '.details'},
}
HTML = '''<table><tr><th>Name</th><th>Author</th></tr>
<tr><td><a href="/item?id=1">First item</a><span class="details">Hidden detail
<a href="/item?id=90">Other item</a></span></td><td>Author A</td></tr>
<tr><td><a href="/item?id=2">Second item</a><span class="details">Hidden detail
<a href="/item?id=91">Other item</a></span></td><td>Author B</td></tr></table>'''


def result(html, group=GROUP):
    metrics = evaluate_composite_acceptance(
        html,
        {'version': 1, 'groups': [group]},
        base_url='https://example.test/',
        table_content=html,
    )
    return metrics['composite_acceptance_pass']


def test_scope_allows_real_rows_without_hidden_details():
    assert result(HTML)


def test_hidden_details_cannot_supply_a_missing_visible_link():
    assert not result(HTML.replace('href="/item?id=1"', ''))


def test_deleted_row_does_not_pass():
    first_row = HTML[:HTML.index('<tr><td><a href="/item?id=2"')]
    assert not result(first_row + '</table>')


def test_scope_removing_real_names_does_not_invent_records():
    group = deepcopy(GROUP)
    group['table_scope']['exclude_selector'] = 'a'
    assert not result(HTML, group)


def test_unscoped_behavior_and_markdown_unchanged():
    group = deepcopy(GROUP)
    group.pop('table_scope')
    assert not result(HTML, group)
    markdown = (
        '|Name|Author|\n|---|---|\n'
        '|[First item](/item?id=1)|Author A|\n'
        '|[Second item](/item?id=2)|Author B|'
    )
    assert result(markdown) and result(markdown, group)


@pytest.mark.parametrize('scope', [
    {},
    {'exclude_selector': ''},
    {'exclude_selector': 3},
    {'exclude_selector': '.details', 'other': True},
])
def test_invalid_scope_rejected(scope):
    group = deepcopy(GROUP)
    group['table_scope'] = scope
    with pytest.raises(ValueError):
        validate_composite_acceptance({'version': 1, 'groups': [group]})


def test_scope_rejected_on_other_group_kinds():
    group = {
        'id': 'links',
        'kind': 'named_link',
        'minimum_records': 1,
        'hosts': ['example.test'],
        'entry_path': '/item',
        'table_scope': {'exclude_selector': '.details'},
    }
    with pytest.raises(ValueError):
        validate_composite_acceptance({'version': 1, 'groups': [group]})


@pytest.mark.parametrize('serialization', ['html', 'markdown'])
def test_eight_row_boundary_requires_eight_real_destinations(serialization):
    group = deepcopy(GROUP)
    group['minimum_records'] = 8
    if serialization == 'html':
        rows = ''.join(
            f'<tr><td><a href="/item?id={i}">Item {i}</a>'
            f'<span class="details"><a href="/item?id=extra{i}">'
            f'Hidden detail</a></span></td><td>Author {i}</td></tr>'
            for i in range(8)
        )
        source = '<table><tr><th>Name</th><th>Author</th></tr>' + rows + '</table>'
        missing = source.replace('href="/item?id=0"', '')
    else:
        rows = '\n'.join(
            f'|[Item {i}](/item?id={i})|Author {i}|'
            for i in range(8)
        )
        source = '|Name|Author|\n|---|---|\n' + rows
        missing = source.replace('[Item 0](/item?id=0)', 'Item 0')
    assert result(source, group)
    assert not result(missing, group)
