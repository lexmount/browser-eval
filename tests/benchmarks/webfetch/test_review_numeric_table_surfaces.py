"""Table cells retain numeric grammar, logical ownership and rendered visibility."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import evaluate_stable_task


def scored(case, artifact):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    return task_passed(True, metrics, 'body'), metrics


def composite_case(kind):
    group = {'id': 'items', 'kind': kind, 'minimum_records': 1, 'identity_column': 'name',
             'required_columns': [{'id': 'name', 'kind': 'text', 'aliases': ['Name']},
                                  {'id': 'count', 'kind': 'integer', 'aliases': ['Count']}]}
    if kind == 'linked_table':
        group.update(hosts=['example.test'], entry_path=r'/item/\d+', link_column='name')
    return {'url': 'https://example.test/table', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


@pytest.mark.parametrize('kind', ['table', 'linked_table'])
@pytest.mark.parametrize('value', ['1..2', '12,', '1,2,3', '12.34', '1,234.567'])
def test_composite_integer_cells_reject_malformed_or_decimal_values(kind, value):
    case = composite_case(kind)
    name = '[Alpha](/item/1)' if kind == 'linked_table' else 'Alpha'
    assert not scored(case, '|Name|Count|\n|---|---|\n|' + name + '|' + value + '|')[0]


@pytest.mark.parametrize('kind', ['table', 'linked_table'])
@pytest.mark.parametrize('value', ['12', '1,234', '1.234', '12,345,678', '12.345.678'])
def test_composite_integer_cells_preserve_supported_grouping(kind, value):
    case = composite_case(kind)
    name = '[Alpha](/item/1)' if kind == 'linked_table' else 'Alpha'
    assert scored(case, '|Name|Count|\n|---|---|\n|' + name + '|' + value + '|')[0]


@pytest.mark.parametrize('kind', ['table', 'linked_table'])
@pytest.mark.parametrize('divider', ['|---|', '|---|---|---|'])
def test_composite_table_delimiter_width_must_match_header(kind, divider):
    case = composite_case(kind)
    name = '[Alpha](/item/1)' if kind == 'linked_table' else 'Alpha'
    artifact = '|Name|Count|\n{divider}\n|' + name + '|12|'
    assert not scored(case, artifact.format(divider=divider))[0]
    assert scored(case, artifact.format(divider='|---|---|'))[0]


def stable_case(schema, **extra):
    return {'url': 'https://example.test/table', 'task_kind': 'body', 'min_content_chars': 1,
            'acceptance_contract': {'version': 1, 'schema': schema,
                'minimum_records': 0 if schema in {'body_text', 'page_identity'} else 1, **extra}}


@pytest.mark.parametrize('notes', ['', ' '])
@pytest.mark.parametrize('position', ['first', 'last'])
def test_empty_optional_edge_cells_do_not_shift_required_values(notes, position):
    case = stable_case('name_description')
    if position == 'first':
        artifact = ('|Notes|Name|Description|\n|---|---|---|\n|'
                    + notes + '|Alpha|Useful description|')
    else:
        artifact = ('|Name|Description|Notes|\n|---|---|---|\n|Alpha|Useful description|'
                    + notes + '|')
    assert scored(case, artifact)[0]
    assert not scored(case, artifact.replace('Useful description', ''))[0]


@pytest.mark.parametrize('root', ['p', 'table', 'ul', 'div'])
@pytest.mark.parametrize('schema', ['body_text', 'page_identity'])
def test_direct_stable_evaluator_projects_all_supported_html_roots(root, schema):
    case = stable_case(schema, stable_text_anchors=[{'id': 'identity',
                                                     'alternatives': ['Required label']}])
    for attrs, text, passed in [('', 'Required label', True),
                                (' hidden', 'Required label', False),
                                (' data-label="Required label"', 'Other text', False)]:
        artifact = '<' + root + attrs + '>' + text + '</' + root + '>'
        direct = evaluate_stable_task(artifact, case['acceptance_contract'])
        assert direct['stable_task_pass'] is passed
        assert scored(case, artifact)[0] is passed


def table_case(currency=False, selectors=False):
    second = {'name': 'amount' if currency else 'count', 'label': 'Amount' if currency else 'Count',
              'type': 'currency_usd' if currency else 'integer'}
    if currency:
        second['allow_empty'] = True
    columns = [{'name': 'name', 'label': 'Name', 'type': 'text'}, second]
    if selectors:
        for index, column in enumerate(columns, 1):
            column['selector'] = ':scope > td:nth-of-type(' + str(index) + ')'
    scope = {'row_selector': '.data', 'cell_selector': ':scope > td',
             'start_pattern': r'(?m)^(?=\|\s*Name\s*\|)', 'end_pattern': '^End$',
             'plain_row_pattern': r'(?P<name>[A-Za-z]+) (?P<' + second['name'] + r'>\d+)'}
    if currency:
        scope.update(reference_kind='fixed_demo', reference_rows=[{'name': 'Alpha', 'amount': ''}])
    return stable_case('table_row', table_columns=columns,
                       record_identity_fields=['name'], table_scope=scope)


@pytest.mark.parametrize('empty', ['', '   '])
def test_empty_optional_currency_has_a_canonical_empty_value(empty):
    case = table_case(currency=True)
    artifact = '|Name|Amount|\n|---|---|\n|Alpha|' + empty + '|\nEnd'
    assert scored(case, artifact)[0]
    assert not scored(case, artifact.replace('|' + empty + '|\nEnd', '|$12|\nEnd'))[0]


@pytest.mark.parametrize('selectors', [False, True])
@pytest.mark.parametrize('span', ['colspan="2"', 'rowspan="2"', 'rowspan="0"'])
def test_spanning_cells_cannot_supply_positional_fields(selectors, span):
    case = table_case(selectors=selectors)
    artifact = '<table><tr class="data"><td {span}>Alpha</td><td>42</td></tr></table>'
    assert scored(case, artifact.format(span=''))[0]
    assert not scored(case, artifact.format(span=span))[0]


@pytest.mark.parametrize('selectors', [False, True])
def test_unselected_earlier_rowspan_cannot_shift_a_selected_row(selectors):
    case = table_case(selectors=selectors)
    artifact = ('<table><tr><td rowspan="2">Earlier</td></tr>'
                '<tr class="data"><td>Alpha</td><td>42</td></tr>{later}</table>')
    assert not scored(case, artifact.format(later=''))[0]
    assert scored(case, artifact.format(later='<tr class="data"><td>Beta</td><td>43</td></tr>'))[0]


@pytest.mark.parametrize('selectors', [False, True])
def test_scoped_tbody_retains_earlier_rowspan_context(selectors):
    case = table_case(selectors=selectors)
    case['acceptance_contract']['table_scope'].update(root_selector='#selected', root_index=0)
    artifact = ('<table><tbody id="selected"><tr><td rowspan="2">Earlier</td></tr>'
                '<tr class="data"><td>Alpha</td><td>42</td></tr></tbody></table>')
    assert not scored(case, artifact)[0]
    assert scored(case, artifact.replace('rowspan="2"', 'rowspan="1"'))[0]


def test_rowspan_zero_does_not_cross_an_html_row_group():
    case = table_case()
    case['acceptance_contract']['table_scope'].update(root_selector='#selected', root_index=0)
    artifact = ('<table><thead><tr><th rowspan="0">Header</th></tr></thead>'
                '<tbody id="selected"><tr class="data"><td>Alpha</td><td>42</td></tr>'
                '</tbody></table>')
    assert scored(case, artifact)[0]
    in_body = ('<table><tbody id="selected"><tr><td rowspan="0">Earlier</td></tr>'
               '<tr class="data"><td>Alpha</td><td>42</td></tr></tbody></table>')
    assert not scored(case, in_body)[0]


def test_colspan_one_does_not_invalidate_following_ordinary_rows():
    case = table_case()
    artifact = ('<table><tr class="data"><td colspan="1" rowspan="1">Alpha</td><td>42</td></tr>'
                '</table>')
    assert scored(case, artifact)[0]
    assert not scored(case, artifact.replace('colspan="1"', 'colspan="2"'))[0]
