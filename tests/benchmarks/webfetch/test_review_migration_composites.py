"""Composite records retain section, link-label and table-cell ownership."""
import pytest

from lexbench_webfetch.composite_tasks import validate_composite_acceptance
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def composite_case(group):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body',
            'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


def named_group(**extra):
    return {'id': 'items', 'kind': 'named_link', 'minimum_records': 1,
            'hosts': ['example.test'], 'entry_path': r'/item/\d+', **extra}


def linked_group(**extra):
    return {**named_group(), 'kind': 'linked_table', 'required_columns': [
        {'id': 'name', 'kind': 'text', 'aliases': ['Name']},
        {'id': 'owner', 'kind': 'text', 'aliases': ['Owner']}],
        'identity_column': 'name', 'link_column': 'name', **extra}


def passed(case, text):
    metrics = score_attempt(case, text.encode(), ok=True)
    return task_passed(True, metrics, 'body')


@pytest.mark.parametrize('fields', [None, []])
def test_field_reports_reject_missing_or_empty_fields(fields):
    group = {'id': 'report', 'kind': 'field_report'}
    if fields is not None:
        group['required_fields'] = fields
    with pytest.raises(ValueError, match='field report'):
        validate_composite_acceptance({'version': 1, 'groups': [group]})


def test_field_report_has_a_real_labeled_assertion():
    case = composite_case({'id': 'report', 'kind': 'field_report',
                           'required_fields': [{'id': 'total', 'kind': 'integer',
                                                'aliases': ['Total']}]})
    assert passed(case, 'Total: 12')
    assert not passed(case, 'Total: unknown')


def test_field_report_rejects_fixed_values_without_a_declared_field():
    group = {'id': 'report', 'kind': 'field_report',
             'required_fields': [{'id': 'total', 'kind': 'integer', 'aliases': ['Total']}],
             'fixed_values': {'unknown': '12'}}
    with pytest.raises(ValueError, match='field report'):
        validate_composite_acceptance({'version': 1, 'groups': [group]})


@pytest.mark.parametrize('layout', ['table', 'linearized', 'named', 'wildcard'])
def test_malformed_url_is_a_failed_record_and_keeps_valid_siblings(layout):
    group = named_group() if layout in {'named', 'wildcard'} else linked_group()
    if layout == 'wildcard':
        group['hosts'] = ['*']
    case = composite_case(group)
    if layout == 'table':
        source = '|Name|Owner|\n|---|---|\n|[Item]({url})|Alice|'
    elif layout == 'linearized':
        source = 'Name\nOwner\n[Item]({url})\n[Alice](/people/alice)'
    else:
        source = '[Item]({url})'
    assert passed(case, source.format(url='/item/1'))
    invalid = source.format(url='https://[invalid/item')
    assert not passed(case, invalid)
    assert passed(case, invalid + '\n\n' + source.format(url='/item/2'))


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
def test_named_link_needs_visible_name_under_both_host_policies(hosts):
    case = composite_case(named_group(hosts=hosts))
    assert not passed(case, '[![Photo](/photo.png)](https://example.test/item/1)')
    assert passed(case, '[Visible name ![Photo](/photo.png)](https://example.test/item/1)')


@pytest.mark.parametrize('outside_first', [False, True])
def test_native_table_duplicate_outside_section_cannot_repair_scoped_row(outside_first):
    case = composite_case(named_group(
        start_pattern=r'^# Selected$', end_pattern=r'^# End$',
        required_fields=[{'id': 'price', 'kind': 'price'}]))
    def row(price):
        return '<table><tr><td><a href="/item/1">Item</a></td><td>' + price + '</td></tr></table>'
    outside = row('$12')
    def page(price):
        selected = '<h1>Selected</h1>' + row(price) + '<h1>End</h1>'
        return '<main>' + (outside + selected if outside_first else selected + outside) + '</main>'
    assert passed(case, page('$34'))
    assert not passed(case, page(''))


def test_linked_table_scope_aggregates_distinct_rows_across_tables():
    case = composite_case(linked_group(minimum_records=2,
        start_pattern=r'^# Selected$', end_pattern=r'^# End$'))
    table = '|Name|Owner|\n|---|---|\n|[Item {id}](/item/{id})|Alice|'
    def page(second):
        return '# Selected\n' + table.format(id=1) + '\n\n## More\n' + second + '\n# End'
    assert passed(case, page(table.format(id=2)))
    assert not passed(case, page(table.format(id=1)))
    assert not passed(case, page(table.format(id=2).replace('|Alice|', '||')))


def table_case(selector_fields=False):
    columns = [{'name': 'name', 'label': 'Name', 'type': 'text'},
               {'name': 'count', 'label': 'Count', 'type': 'integer'}]
    if selector_fields:
        for index, column in enumerate(columns, 1):
            column['selector'] = f':scope > td:nth-of-type({index})'
    return {'url': 'https://example.test/table', 'task_kind': 'body', 'min_content_chars': 1,
            'acceptance_contract': {'version': 1, 'schema': 'table_row', 'minimum_records': 1,
                'table_columns': columns, 'record_identity_fields': ['name'],
                'table_scope': {'row_selector': '#outer > tbody > tr',
                    'cell_selector': ':scope > td', 'start_pattern': '^Name$',
                    'end_pattern': '^End$',
                    'plain_row_pattern': r'(?P<name>[A-Za-z]+) (?P<count>\d+)',
                    'reference_kind': 'fixed_demo',
                    'reference_rows': [{'name': 'Alpha', 'count': '12'}]}}}


@pytest.mark.parametrize('selector_fields', [False, True])
@pytest.mark.parametrize('nested_inside_link', [False, True])
def test_nested_tables_cannot_supply_or_corrupt_outer_fields(selector_fields, nested_inside_link):
    case = table_case(selector_fields)
    def page(name, nested_name='Unrelated'):
        nested = '<table><tr><td>' + nested_name + '</td></tr></table>'
        cell = ('<a href="/item/1">' + name + nested + '</a>'
                if nested_inside_link else name + nested)
        return '<table id="outer"><tbody><tr><td>' + cell + '</td><td>12</td></tr></tbody></table>'
    assert passed(case, page('Alpha'))
    assert not passed(case, page('', 'Alpha'))
