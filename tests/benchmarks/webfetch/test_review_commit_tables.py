"""A single row check preserves five fields and distinct commit destinations."""
import pytest

from lexbench_webfetch import benchmark
from lexbench_webfetch.composite_tasks import fields_are_valid
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


@pytest.fixture(scope='module')
def reviewed_cases():
    root = benchmark.suite('access-restrictions')[1]
    return benchmark.apply_annotation_revision(
        'access-restrictions', benchmark.read(root / 'tasks.json'), 'review-550-20261023')['cases']


@pytest.mark.parametrize('case_id', ['risk-diverse-027', 'risk-diverse-050'])
@pytest.mark.parametrize('variant,expected', [
    ('complete', True), ('new-messages', True), ('seven', False),
    ('same-url', False), ('age', False), ('message', False),
    ('author', False), ('files', False), ('lines', False),
    ('invalid-lines', False),
])
def test_reviewed_commit_table(reviewed_cases, case_id, variant, expected):
    case = next(c for c in reviewed_cases if c['id'] == case_id)
    contract = case['acceptance_contract']
    assert contract['schema'] == 'body_text'
    group = case['composite_acceptance']['groups'][0]
    assert len(group['required_columns']) == 5
    rows = []
    for index in range(7 if variant == 'seven' else 8):
        identity = 0 if variant == 'same-url' else index
        label = f'Newly published change {index}' if variant == 'new-messages' else f'Commit {index}'
        row = {'age': '2 days', 'message': f'[{label}]({case["url"]}commit/?id={identity})',
               'author': 'Example Author', 'files': '3', 'lines': '-17 / +29'}
        if variant in row:
            row[variant] = ''
        if variant == 'invalid-lines':
            row['lines'] = 'arbitrary text -17 / +29 extra'
        rows.append('| ' + ' | '.join(row.values()) + ' |')
    anchors = '\n'.join(a['alternatives'][0] for a in contract['stable_text_anchors'])
    answer = anchors + '\n\n| Age | Commit message | Author | Files | Lines |\n'
    answer += '| --- | --- | --- | --- | --- |\n' + '\n'.join(rows)
    assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is expected


@pytest.mark.parametrize('field,valid', [
    ({'id': 'lines', 'kind': 'token', 'pattern': r'-\d+ / \+\d+'}, True),
    ({'id': 'lines', 'kind': 'token'}, False),
    ({'id': 'lines', 'kind': 'token', 'pattern': ''}, False),
    ({'id': 'lines', 'kind': 'token', 'pattern': 17}, False),
])
def test_token_fields_require_an_explicit_pattern(field, valid):
    assert fields_are_valid([field]) is valid
