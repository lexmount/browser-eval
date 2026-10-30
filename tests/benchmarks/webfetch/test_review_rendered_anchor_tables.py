"""Rendered body anchors and literal table delivery retain their representation."""
from copy import deepcopy
from html import escape

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task


CONTROLS = []
for literal in ['[Widget](/other)', '**Widget**', '`Widget`', '<b>Widget</b>']:
    contract = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'stable_text_anchors': [{'id': 'literal-label', 'alternatives': [literal]}]}
    for representation in ['html', None]:
        for body, passed in [(literal, True), ('Widget', False)]:
            CONTROLS.append((f'body-{len(CONTROLS)}', '<p>' + escape(body) + '</p>',
                             contract, representation, passed))
for reference in ['Widget']:
    contract = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'stable_text_anchors': [{'id': 'rendered-label', 'alternatives': [reference]}]}
    CONTROLS.append((f'actual-format-{len(CONTROLS)}', '<p><b>Widget</b></p>', contract, 'html', True))

TABLE = '<table><tr class="data"><td>Alpha</td><td>12</td></tr></table>'
TSV = 'Name\tCount\nAlpha\t12\nEnd'
TABLE_CONTRACT = {
    'version': 1, 'schema': 'table_row', 'minimum_records': 1,
    'table_columns': [{'name': 'name', 'label': 'Name', 'type': 'text'},
                      {'name': 'count', 'label': 'Count', 'type': 'integer'}],
    'record_identity_fields': ['name'], 'table_scope': {
        'row_selector': '.data', 'cell_selector': ':scope > td',
        'start_pattern': '^Name\tCount$', 'end_pattern': '^End$',
        'plain_row_pattern': r'(?P<name>[^\t\r\n]+)\t(?P<count>\d+)'},
}
for representation, text, passed in [('plain_text', TABLE, False), ('html', TABLE, True),
                                     (None, TABLE, True), ('plain_text', TSV, True)]:
    CONTROLS.append((f'table-{len(CONTROLS)}', text, TABLE_CONTRACT, representation, passed))
for text, reference, representation, passed in [
    (TSV, TSV, 'plain_text', True),
    (TSV, TSV.replace('Alpha', 'Beta'), 'plain_text', False),
    (TSV, TABLE, 'plain_text', True),
    (TABLE, TSV, 'html', True),
    (TABLE, TSV.replace('Alpha', 'Beta'), 'html', False),
    (TABLE, TSV.replace('Alpha', '**Alpha**'), 'html', False),
    (TABLE.replace('Alpha', '**Alpha**'), TSV.replace('Alpha', '**Alpha**'), 'html', True),
    (TABLE, TSV.replace('Alpha', '[Alpha](/other)'), 'html', False),
    (TABLE.replace('Alpha', '[Alpha](/other)'), TSV.replace('Alpha', '[Alpha](/other)'), 'html', True),
]:
    contract = deepcopy(TABLE_CONTRACT)
    contract['stable_text_anchors'] = [{'id': 'required-row', 'alternatives': [reference]}]
    CONTROLS.append((f'table-anchor-{len(CONTROLS)}', text, contract, representation, passed))


@pytest.mark.parametrize('identifier,text,contract,representation,passed', CONTROLS)
def test_rendered_anchor_and_table_delivery(identifier, text, contract, representation, passed):
    assert evaluate_stable_task(text, contract, representation=representation)['stable_task_pass'] is passed
    metadata = {'answer_format': representation} if representation else {}
    case = {'url': 'https://example.test/', 'acceptance_contract': contract}
    assert evaluate_response(case, text.encode(), metadata=metadata)['passed'] is passed


def scoring_controls():
    for identifier, text, contract, representation, passed in CONTROLS:
        yield {'id': 'runtime17-' + identifier, 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'rendered_text_is_literal_and_table_delegate_retains_delivery_representation',
               'case': {'url': 'https://example.test/', 'acceptance_contract': contract},
               'artifact': text, 'metadata': {'answer_format': representation} if representation else {},
               'ok': True, 'expected_task_pass': passed}
