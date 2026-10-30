"""Caption and column projections retain real table and row boundaries."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.structured_tables import table_header_column_surfaces


def scored(case, artifact):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    return task_passed(True, metrics, 'body'), metrics


def case_for(group):
    return {'url': 'https://example.test/table', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


def test_optional_combined_capture_rejects_incomplete_matching_row():
    case = case_for({'id': 'items', 'kind': 'table', 'minimum_records': 1,
        'identity_column': 'name', 'required_columns': [
            {'id': 'name', 'kind': 'text', 'aliases': ['Name']},
            {'id': 'owner', 'kind': 'text', 'aliases': ['Owner']}],
        'combined_columns': [{'aliases': ['Person'],
                             'pattern': r'(?P<name>[^;]+)(?:;(?P<owner>.+))?'}]})
    assert not scored(case, '|Person|\n|---|\n|Widget|')[0]
    assert scored(case, '|Person|\n|---|\n|Widget;Alice|')[0]


def caption_group():
    return {'id': 'people', 'kind': 'table', 'minimum_records': 2, 'identity_column': 'id',
        'required_columns': [{'id': 'id', 'kind': 'integer', 'aliases': ['ID']},
                             {'id': 'name', 'kind': 'text', 'aliases': ['Name']}],
        'group_caption': {'table_selector': 'table.people', 'row_selector': 'tr.person',
            'cell_selector': 'td', 'caption_selector': 'tr.caption',
            'pattern': r'Country: (?P<value>[A-Za-z]+)',
            'field': {'id': 'country', 'kind': 'text', 'aliases': ['Country']}}}


def html_caption():
    return '<tr class="caption"><td colspan="2">Country: Argentina</td></tr>'


def html_rows(first_attrs=''):
    return ('<tr class="person"><td ' + first_attrs + '>1</td><td>Alpha</td></tr>'
            '<tr class="person"><td>2</td><td>Beta</td></tr>')


@pytest.mark.parametrize('attribute', ['rowspan="2"', 'rowspan="0"', 'colspan="2"'])
def test_caption_expansion_rejects_spanning_record_rows(attribute):
    case = case_for(caption_group())
    artifact = '<table class="people">' + html_caption() + '{rows}</table>'
    assert scored(case, artifact.format(rows=html_rows()))[0]
    assert not scored(case, artifact.format(rows=html_rows(attribute)))[0]


def test_excluded_record_row_retains_span_context_during_caption_expansion():
    group = caption_group()
    group['table_scope'] = {'exclude_selector': '.ignored'}
    case = case_for(group)
    artifact = ('<table class="people">' + html_caption()
        + '<tr class="ignored"><td rowspan="{span}">Earlier</td></tr>'
        + html_rows() + '</table>')
    assert not scored(case, artifact.format(span='2'))[0]
    assert scored(case, artifact.format(span='1'))[0]


@pytest.mark.parametrize('gap', ['\n', '\n\n'])
def test_caption_context_resets_at_every_markdown_table(gap):
    case = case_for(caption_group())
    caption_only = '|ID|Name|\n|---|---|\n|Country: Argentina|'
    records = '|ID|Name|\n|---|---|\n|1|Alpha|\n|2|Beta|'
    assert not scored(case, caption_only + gap + records)[0]
    assert scored(case, caption_only + gap + records.replace('|1|Alpha|',
                                                             '|Country: Brazil|\n|1|Alpha|'))[0]


def test_normalized_table_does_not_swallow_a_following_caption_table():
    case = case_for(caption_group())
    first = '|ID|Name|Country|\n|---|---|---|\n|1|Alpha|Argentina|\n'
    second = '|ID|Name|\n|---|---|\n|Country: Brazil|\n|2|Beta|'
    assert scored(case, first + second)[0]
    assert not scored(case, first + second.replace('|Country: Brazil|\n', ''))[0]


def body_column_case():
    return {'url': 'https://example.test/tables', 'task_kind': 'body', 'min_content_chars': 1,
        'body_requirements': {'version': 3, 'unit': 'region_compact_hexagram_recall',
            'minimum_coverage': 1., 'minimum_floor': 1., 'minimum_region_passes': 1,
            'regions': [{'id': 'names', 'text': 'Name Amount Alpha Name Beta'}]}}


def test_header_column_fallback_cannot_join_adjacent_tables():
    case = body_column_case()
    first = '|Name|Amount|\n|---|---|\n|Alpha|X|\n'
    second = '|Name|Amount|\n|---|---|\n|Beta|Y|'
    assert not scored(case, first + second)[0]
    assert scored(case, first + '|Name|X|\n|Beta|Y|')[0]
    surfaces = table_header_column_surfaces(first + second)
    assert {surface['table_index'] for surface in surfaces} == {1, 2}
    assert all(not ('Alpha' in surface['text'] and 'Beta' in surface['text'])
               for surface in surfaces)
