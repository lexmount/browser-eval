"""Composite dispatch preserves literal input and rendered native content."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response


def task(group):
    return {'url': 'https://example.test/', 'composite_acceptance': {'version': 1, 'groups': [group]}}


def table_group(kind='table'):
    group = {'id': 'rows', 'kind': kind, 'minimum_records': 1, 'identity_column': 'id',
             'required_columns': [{'id': 'id', 'kind': 'integer', 'aliases': ['ID']},
                                  {'id': 'name', 'kind': 'text', 'aliases': ['Name']}]}
    if kind == 'linked_table':
        group.update(hosts=['example.test'], entry_path=r'/item/1', link_column='name')
    return group


def cases():
    shapes = [
        ('name_description', {'id': 'description', 'kind': 'name_description', 'minimum_records': 1},
         '<section><p>Widget</p><p>Durable device</p></section>'),
        ('table', table_group(), '<table><tr><th>ID</th><th>Name</th></tr><tr><td>1</td><td>Widget</td></tr></table>'),
        ('linked_table', table_group('linked_table'), '<table><tr><th>ID</th><th>Name</th></tr><tr><td>1</td><td><a href="/item/1">Widget</a></td></tr></table>'),
        ('named_link', {'id': 'named', 'kind': 'named_link', 'minimum_records': 1, 'hosts': ['example.test'], 'entry_path': r'/item/1'},
         '<a href="/item/1">Widget</a>'),
        ('field_report', {'id': 'fields', 'kind': 'field_report', 'required_fields': [
            {'id': 'status', 'kind': 'token', 'aliases': ['Status'], 'pattern': 'READY'}]},
         '<section><p>Status:READY</p></section>'),
    ]
    for identifier, group, text in shapes:
        for representation, expected in [('plain_text', False), ('html', True)]:
            yield identifier + '-' + representation, text, task(group), representation, expected
    for representation, expected in [('plain_text', True), ('html', False)]:
        yield 'literals-' + representation, '<a href="/Alpha">Other</a>', task(
            {'id': 'literal', 'kind': 'literals', 'required_literals': ['Alpha']}), representation, expected
    yield 'plain-description-lines', 'Widget\nDurable device', task(shapes[0][1]), 'plain_text', True
    yield 'plain-tsv', 'ID\tName\n1\tWidget', task(table_group()), 'plain_text', True
    literal_table = table_group()
    literal_table['required_columns'][1] = {'id': 'name', 'kind': 'token', 'aliases': ['Name'], 'pattern': re.escape('**READY**')}
    for representation, expected in [('plain_text', True), ('markdown', False)]:
        yield 'table-literal-value-' + representation, 'ID\tName\n1\t**READY**', task(literal_table), representation, expected
    yield 'rendered-description-literal-tags', '<section><p>&lt;b&gt;Widget&lt;/b&gt;</p><p>Durable device</p></section>', task(shapes[0][1]), 'html', True


@pytest.mark.parametrize('identifier,text,case,representation,expected', list(cases()))
def test_each_composite_dispatch_keeps_the_declared_format(identifier, text, case, representation, expected):
    result = evaluate_response(case, text.encode(), metadata={'answer_format': representation})
    assert result['measured']
    assert result['passed'] is expected
    if identifier == 'rendered-description-literal-tags':
        assert result['metrics']['composite_group_results'][0]['records'][0]['fields']['name'] == '<b>Widget</b>'


@pytest.mark.parametrize('representation,expected', [('plain_text', False), ('html', True)])
def test_caption_projection_does_not_parse_literal_html(representation, expected):
    from .test_table_group_caption import contract, table, caption, row
    case = {'url': 'https://example.test/', 'composite_acceptance': contract()}
    text = table(caption() + row(1) + row(2))
    assert evaluate_response(case, text.encode(), metadata={'answer_format': representation})['passed'] is expected


def scoring_controls():
    selected = {'name_description-plain_text', 'name_description-html', 'table-plain_text', 'table-html',
                'plain-tsv', 'rendered-description-literal-tags', 'table-literal-value-plain_text', 'table-literal-value-markdown'}
    for identifier, text, case, representation, expected in cases():
        if identifier in selected:
            yield {'id': 'composite-dispatch-' + identifier, 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Composite description/table delegates preserve declared plain text and do not reinterpret already-rendered HTML values.',
                   'case': case, 'artifact': text, 'metadata': {'answer_format': representation},
                   'ok': True, 'expected_task_pass': expected}
