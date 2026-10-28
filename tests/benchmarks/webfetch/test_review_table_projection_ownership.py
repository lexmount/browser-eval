"""Every table projection retains the same visible identities and row ownership."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def scored(case, artifact):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    return task_passed(True, metrics, 'body'), metrics


def composite_case(group):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


def test_native_named_row_cannot_borrow_a_nested_tables_price():
    case = composite_case({'id': 'items', 'kind': 'named_link', 'minimum_records': 1,
        'hosts': ['example.test'], 'entry_path': r'/item/\d+',
        'required_fields': [{'id': 'price', 'kind': 'price'}]})
    def page(own):
        return ('<table><tr><td><a href="/item/1">Widget</a></td><td>' + own
                + '<table><tr><td>$10</td></tr></table></td></tr></table>')
    assert not scored(case, page(''))[0]
    assert scored(case, page('$20'))[0]


@pytest.mark.parametrize('value', ['[Alice](/profile/a)', '[Alice](/profile/b)',
                                   '**Alice**', '__Alice__', '_Alice_'])
def test_composite_table_identity_uses_the_visible_scalar(value):
    case = composite_case({'id': 'people', 'kind': 'table', 'minimum_records': 2,
        'identity_column': 'name', 'required_columns': [
            {'id': 'name', 'kind': 'text', 'aliases': ['Name']},
            {'id': 'description', 'kind': 'text', 'aliases': ['Description']}]})
    artifact = '|Name|Description|\n|---|---|\n|Alice|First person|\n|{second}|Second person|'
    assert not scored(case, artifact.format(second=value))[0]
    assert scored(case, artifact.format(second='[Bob](/profile/b)'))[0]


def table_group(kind='table'):
    group = {'id': 'items', 'kind': kind, 'minimum_records': 1, 'identity_column': 'name',
        'required_columns': [{'id': 'name', 'kind': 'text', 'aliases': ['Name']},
                             {'id': 'count', 'kind': 'integer', 'aliases': ['Count']}]}
    if kind == 'linked_table':
        group.update(hosts=['example.test'], entry_path=r'/item/\d+', link_column='name')
    return group


@pytest.mark.parametrize('kind', ['table', 'linked_table'])
@pytest.mark.parametrize('span', ['2', '0'])
def test_composite_html_converter_rejects_rows_displaced_by_an_earlier_span(kind, span):
    case = composite_case(table_group(kind))
    name = '<a href="/item/1">Alpha</a>' if kind == 'linked_table' else 'Alpha'
    artifact = ('<table><tr><th>Name</th><th>Count</th></tr>'
                '<tr><td rowspan="{span}">Earlier</td></tr>'
                '<tr><td>' + name + '</td><td>42</td></tr></table>')
    assert not scored(case, artifact.format(span=span))[0]
    assert scored(case, artifact.format(span='1'))[0]


def structured_case():
    return {'url': 'https://example.test/table', 'task_kind': 'body', 'min_content_chars': 1,
        'acceptance_contract': {'version': 1, 'schema': 'table_row',
            'minimum_records': 2, 'maximum_records': 2,
            'table_columns': [{'name': 'name', 'label': 'Name', 'type': 'text'},
                              {'name': 'count', 'label': 'Count', 'type': 'integer'}],
            'record_identity_fields': ['name'], 'table_scope': {
                'row_selector': '.data', 'cell_selector': ':scope > td',
                'start_pattern': '^Records$', 'end_pattern': '^End$',
                'plain_row_pattern': r'(?P<name>[A-Za-z]+) (?P<count>\d+)'}}}


@pytest.mark.parametrize('reordered', [False, True])
def test_adjacent_structured_tables_get_independent_header_mappings(reordered):
    case = structured_case()
    first = 'Records\n|Name|Count|\n|---|---|\n|Alpha|12|\n'
    second = ('|Count|Name|\n|---|---|\n|34|Beta|' if reordered else
              '|Name|Count|\n|---|---|\n|Beta|34|')
    passed, metrics = scored(case, first + second + '\nEnd')
    assert passed
    assert [row['fields'] for row in metrics['stable_records']] == [
        {'name': 'Alpha', 'count': '12'}, {'name': 'Beta', 'count': '34'}]
    assert not scored(case, first + second.replace('34', '') + '\nEnd')[0]


@pytest.mark.parametrize('kind', ['table', 'linked_table', 'table_row'])
def test_excluded_auxiliary_row_still_supplies_rowspan_context(kind):
    if kind == 'table_row':
        case = structured_case()
        case['acceptance_contract'].update(minimum_records=1, maximum_records=1)
        case['acceptance_contract']['table_scope']['exclude_selector'] = '.ignore'
    else:
        group = table_group(kind)
        group['table_scope'] = {'exclude_selector': '.ignore'}
        case = composite_case(group)
    name = '<a href="/item/1">Alpha</a>' if kind == 'linked_table' else 'Alpha'
    artifact = ('<table><tr><th>Name</th><th>Count</th></tr>'
        '<tr class="ignore"><td rowspan="{span}">Earlier</td></tr>'
        '<tr class="data"><td>' + name + '</td><td>42</td></tr></table>')
    assert not scored(case, artifact.format(span='2'))[0]
    assert scored(case, artifact.format(span='1'))[0]
