from copy import deepcopy
import pytest
from lexbench_webfetch.annotation_revisions import move_regions_to_stable_text
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed

def case():
    return {'url': 'https://example.test/table', 'task_kind': 'body', 'min_content_chars': 0,
        'body_requirements': {'version': 3, 'unit': 'region_compact_hexagram_recall',
            'minimum_coverage': .8, 'minimum_floor': .8, 'minimum_region_passes': 2,
            'regions': [{'id': 'prose', 'text': 'This table contains the initial published records.'}, {'id': 'button', 'text': 'view source'}]},
        'acceptance_contract': {'version': 1, 'schema': 'table_row', 'minimum_records': 2,
            'stable_text_anchors': [{'id': 'identity', 'alternatives': ['Published records']}],
            'table_columns': [{'name': 'name', 'label': 'Name', 'type': 'text'}, {'name': 'count', 'label': 'Count', 'type': 'integer'}],
            'table_scope': {'row_selector': 'tbody tr', 'cell_selector': 'td', 'start_pattern': 'Published records', 'end_pattern': 'View Source', 'plain_row_pattern': r'(?P<name>[A-Za-z]+) (?P<count>\d+)'}, 'record_identity_fields': ['name']}}

def move(c):
    move_regions_to_stable_text(c, [{'region_id': 'button', 'anchor_id': 'button-label', 'text': 'view source'}])

def passed(c, s):
    m = score_attempt(c, s.encode(), ok=True, status_code=200, final_url=c['url'])
    return task_passed(True, m, c['task_kind'])

TEXT = 'Published records\nThis table contains the initial published records.\n\n| Name | Count |\n|---|---|\n| Alpha | 12 |\n| Beta | 34 |\n\nView Source'

def test_table_role_preserves_fields_rows_anchors_and_prose():
    c = case(); original = deepcopy(c); move(c)
    assert {k:v for k,v in c['acceptance_contract'].items() if k!='stable_text_anchors'} == {k:v for k,v in original['acceptance_contract'].items() if k!='stable_text_anchors'}
    assert c['acceptance_contract']['stable_text_anchors'][:-1] == original['acceptance_contract']['stable_text_anchors']
    assert c['body_requirements']['regions'] == original['body_requirements']['regions'][:1]
    assert passed(c, TEXT)
    for missing in ['View Source', '| Beta | 34 |', 'Published records', 'This table contains the initial published records.']:
        assert not passed(c, TEXT.replace(missing, ''))
    assert not passed(c, TEXT.replace('| Beta | 34 |', '| Beta | |'))

@pytest.mark.parametrize('schema', ['named_link', 'composite', 'unknown', None])
def test_unsupported_schema_rejected_atomically(schema):
    c = case(); c['acceptance_contract']['schema'] = schema; original = deepcopy(c)
    with pytest.raises(ValueError): move(c)
    assert c == original
