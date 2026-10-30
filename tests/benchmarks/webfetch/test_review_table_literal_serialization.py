"""Native cell text is serialized without activating Markdown syntax."""
from html import escape
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.structured_tables import _visible_markdown_scalar


def case(expected, minimum=1):
    return {'url': 'https://example.test/', 'composite_acceptance': {'version': 1, 'groups': [{
        'id': 'cells', 'kind': 'table', 'minimum_records': minimum, 'identity_column': 'value',
        'required_columns': [{'id': 'value', 'kind': 'token', 'aliases': ['Value'], 'pattern': expected}]}]}}


def table(*cells):
    return '<table><tr><th>Value</th></tr>' + ''.join('<tr><td>' + c + '</td></tr>' for c in cells) + '</table>'


VALUES = ['**READY**', '__READY__', '`READY`', '[READY](/other)', '<value>', '&amp;', r'A\|B']
VALUES.append('**READY** [READY](/other) `READY` &amp; <tag> ' + r'A\|B')


@pytest.mark.parametrize('value', VALUES)
@pytest.mark.parametrize('representation', [None, 'html'])
def test_native_literal_cell_matches_its_displayed_value_and_returns_it(value, representation):
    result = evaluate_response(case(re.escape(value)), table(escape(value)).encode(),
                               metadata={'answer_format': representation} if representation else None)
    assert result['passed']
    assert result['metrics']['composite_group_results'][0]['records'] == [{'value': value}]


@pytest.mark.parametrize('value', VALUES[:4])
def test_native_literal_format_does_not_become_its_rendered_markdown_value(value):
    assert not evaluate_response(case('READY'), table(escape(value)).encode(), metadata={'answer_format': 'html'})['passed']


@pytest.mark.parametrize('markup', ['<strong>READY</strong>', '<code>READY</code>', '<a href="/other">READY</a>'])
def test_actual_native_formatting_and_links_keep_their_visible_label(markup):
    result = evaluate_response(case('READY'), table(markup).encode(), metadata={'answer_format': 'html'})
    assert result['passed']
    assert result['metrics']['composite_group_results'][0]['records'] == [{'value': 'READY'}]


@pytest.mark.parametrize('markup', ['**READY**', '`READY`', '[READY](/other)'])
def test_genuine_markdown_table_formatting_remains_formatting(markup):
    result = evaluate_response(case('READY'), ('|Value|\n|---|\n|' + markup + '|').encode(), metadata={'answer_format': 'markdown'})
    assert result['passed']
    assert result['metrics']['composite_group_results'][0]['records'] == [{'value': 'READY'}]


@pytest.mark.parametrize('second,expected', [('READY', True), ('**READY**', False)])
def test_native_literal_and_unformatted_rows_have_distinct_identities(second, expected):
    result = evaluate_response(case(r'(?:\*\*READY\*\*|READY)', 2), table('**READY**', second).encode())
    assert result['passed'] is expected
    assert {row['value'] for row in result['metrics']['composite_group_results'][0]['records']} == {'**READY**', second}


def test_native_caption_capture_remains_a_literal_cell_value():
    from .test_table_group_caption import contract, caption, row, table as caption_table
    rules = contract()
    rules['groups'][0]['group_caption'].update(pattern=r'Country: (?P<value>.+) - 2 items',
        field={'id': 'country', 'kind': 'token', 'aliases': ['Country'], 'pattern': re.escape('**READY**')})
    text = caption_table(caption('**READY**') + row(1) + row(2))
    result = evaluate_response({'url': 'https://example.test/', 'composite_acceptance': rules}, text.encode())
    assert result['passed']
    assert {r['country'] for r in result['metrics']['composite_group_results'][0]['records']} == {'**READY**'}


def linked_case(expected):
    return {'url': 'https://example.test/', 'composite_acceptance': {'version': 1, 'groups': [{
        'id': 'linked', 'kind': 'linked_table', 'minimum_records': 1, 'link_column': 'name', 'identity_column': 'name',
        'hosts': ['example.test'], 'entry_path': '/item/1',
        'required_columns': [{'id': 'name', 'kind': 'text', 'aliases': ['Name']},
            {'id': 'owner', 'kind': 'token', 'aliases': ['Owner'], 'pattern': re.escape(expected)}]}]}}


LINKED = '<table><tr><th>Name</th><th>Owner</th></tr><tr><td><a href="/item/1">Widget</a></td><td>Alice | Bob</td></tr></table>'


@pytest.mark.parametrize('expected,passed', [('Alice | Bob', True), ('Alice', False)])
def test_linked_table_keeps_raw_link_ownership_then_stores_visible_fields(expected, passed):
    result = evaluate_response(linked_case(expected), LINKED.encode(), metadata={'answer_format': 'html'})
    assert result['passed'] is passed
    if passed:
        assert result['metrics']['composite_group_results'][0]['records'][0]['fields'] == {'name': 'Widget', 'owner': 'Alice | Bob'}


@pytest.mark.parametrize('source,expected', [(r'\*\*READY\*\*', '**READY**'),
    (r'\`READY\`', '`READY`'), (r'\[READY\]\(/other\)', '[READY](/other)'),
    ('**READY**', 'READY'), ('`READY`', 'READY'), ('[READY](/other)', 'READY'),
    ('`**READY**`', '**READY**'), (r'`\*READY\*`', r'\*READY\*')])
def test_scalar_reader_restores_escapes_after_rendering(source, expected):
    assert _visible_markdown_scalar(source) == expected


def scoring_controls():
    for identifier, cell, expected_value, expected in [
        ('native-bold-literal', '**READY**', r'\*\*READY\*\*', True),
        ('native-bold-not-format', '**READY**', 'READY', False),
        ('native-link-literal', '[READY](/other)', re.escape('[READY](/other)'), True),
        ('native-link-not-label', '[READY](/other)', 'READY', False),
    ]:
        yield {'id': 'table17-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Native cell text is escaped separately from generated links and decoded once after Markdown formatting.',
               'case': case(expected_value), 'artifact': table(escape(cell)), 'ok': True,
               'metadata': {'answer_format': 'html'}, 'expected_task_pass': expected}
    for second, expected in [('READY', True), ('**READY**', False)]:
        yield {'id': 'table17-' + ('distinct' if expected else 'duplicate') + '-native-rows',
               'category': 'answer-surface', 'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Rendered native values are decoded once and retain literal identity at deduplication.',
               'case': case(r'(?:\*\*READY\*\*|READY)', 2),
               'artifact': table('**READY**', second), 'ok': True,
               'metadata': {'answer_format': 'html'}, 'expected_task_pass': expected}
    for value, expected in [('Alice | Bob', True), ('Alice', False)]:
        yield {'id': 'table17-linked-visible-' + ('exact' if expected else 'distinct'),
               'category': 'answer-surface', 'failure_mode': 'false-negative' if expected else 'false-positive',
               'root_cause': 'Linked table ownership uses raw links before decoded visible fields are validated and stored.',
               'case': linked_case(value), 'artifact': LINKED, 'ok': True,
               'metadata': {'answer_format': 'html'}, 'expected_task_pass': expected}
