"""Stable anchors require actual owned table fields and independent page text."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task


EXPECTED = '|Name|Count|\n|---|---|\n|Alpha|12|'
TABLE_CONTRACT = {
    'version': 1, 'schema': 'table_row', 'minimum_records': 1,
    'table_columns': [{'name': 'name', 'label': 'Name', 'type': 'text'},
                      {'name': 'count', 'label': 'Count', 'type': 'integer'}],
    'record_identity_fields': ['name'], 'table_scope': {
        'row_selector': '.data', 'cell_selector': ':scope > td',
        'start_pattern': r'(?m)^(?=\|\s*Name\s*\|)', 'end_pattern': '^End$',
        'plain_row_pattern': r'(?P<name>[A-Za-z]+) (?P<count>\d+)'},
    'stable_text_anchors': [{'id': 'required-row', 'alternatives': [EXPECTED]}],
}
TABLE_CASES = [
    ('table-matching', EXPECTED + '\nEnd', True),
    ('table-unrelated', '|Name|Count|\n|---|---|\n|Beta|34|\nEnd', False),
    ('table-split-fields', '|Name|Count|\n|---|---|\n|Alpha|34|\n|Beta|12|\nEnd', False),
    ('table-wrong-value', EXPECTED.replace('12', '34') + '\nEnd', False),
    ('html-matching', '<table><tr class="data"><td>Alpha</td><td>12</td></tr></table>', True),
    ('html-unrelated', '<table><tr class="data"><td>Beta</td><td>34</td></tr></table>', False),
    ('html-split-fields', '<table><tr class="data"><td>Alpha</td><td>34</td></tr>'
     '<tr class="data"><td>Beta</td><td>12</td></tr></table>', False),
]


def _named_contract(base):
    return {'version': 1, 'schema': 'named_link', 'minimum_records': 1,
            'hosts': ['www.example.com'], 'entry_path': r'/news/item/\d+',
            'link_base_url': base, 'stable_text_anchors': [
                {'id': 'page-heading', 'alternatives': ['Story'], 'match_scope': 'non_record_lines'}]}


NAMED_CASES = []
for label, base in [('host', 'https://redirect.example/start'),
                    ('directory', 'https://www.example.com/redirect/start')]:
    for representation in ['markdown', 'html']:
        record = ('[Story](item/1)' if representation == 'markdown' else
                  '<main><a href="item/1">Story</a></main>')
        for independent, passed in [(False, False), (True, True)]:
            heading = ('# Story\n' if representation == 'markdown' else '<h1>Story</h1>\n') if independent else ''
            NAMED_CASES.append((f'base-{label}-{representation}-{passed}', base,
                                heading + record, _named_contract('https://www.example.com/news/'), passed))


@pytest.mark.parametrize('identifier,text,passed', TABLE_CASES)
def test_table_anchor_values_belong_to_one_row(identifier, text, passed):
    direct = evaluate_stable_task(text, TABLE_CONTRACT)
    assert direct['stable_task_pass'] is passed
    result = evaluate_response({'url': 'https://example.test/table', 'acceptance_contract': TABLE_CONTRACT}, text.encode())
    assert result['passed'] is passed


@pytest.mark.parametrize('name,passed', [('Alpha', True), ('Beta', False)])
def test_html_table_alternative_uses_the_same_columns_for_a_markdown_answer(name, passed):
    contract = dict(TABLE_CONTRACT, stable_text_anchors=[{
        'id': 'required-row', 'alternatives': [TABLE_CASES[4][1]]}])
    text = EXPECTED.replace('Alpha', name) + '\nEnd'
    assert evaluate_stable_task(text, contract)['stable_task_pass'] is passed
    assert evaluate_response({'url': 'https://example.test/table', 'acceptance_contract': contract},
                             text.encode())['passed'] is passed


@pytest.mark.parametrize('identifier,base,text,contract,passed', NAMED_CASES)
def test_non_record_masks_use_the_effective_extraction_base(identifier, base, text, contract, passed):
    direct = evaluate_stable_task(text, contract, base)
    assert direct['stable_record_count'] == 1
    assert direct['stable_task_pass'] is passed
    result = evaluate_response({'url': base, 'acceptance_contract': contract}, text.encode())
    assert result['passed'] is passed


def scoring_controls():
    for identifier, text, passed in TABLE_CASES:
        yield {'id': 'runtime11-' + identifier, 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'table_anchor_comparison_uses_declared_owned_columns',
               'case': {'url': 'https://example.test/table', 'acceptance_contract': TABLE_CONTRACT},
               'artifact': text, 'ok': True, 'expected_task_pass': passed}
    for name, passed in [('Alpha', True), ('Beta', False)]:
        contract = dict(TABLE_CONTRACT, stable_text_anchors=[{
            'id': 'required-row', 'alternatives': [TABLE_CASES[4][1]]}])
        yield {'id': f'runtime11-html-alternative-{name}', 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'table_anchor_comparison_uses_declared_owned_columns',
               'case': {'url': 'https://example.test/table', 'acceptance_contract': contract},
               'artifact': EXPECTED.replace('Alpha', name) + '\nEnd', 'ok': True, 'expected_task_pass': passed}
    for identifier, base, text, contract, passed in NAMED_CASES:
        yield {'id': 'runtime11-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'non_record_anchor_masks_share_the_effective_link_base',
               'case': {'url': base, 'acceptance_contract': contract}, 'artifact': text,
               'ok': True, 'expected_task_pass': passed}
