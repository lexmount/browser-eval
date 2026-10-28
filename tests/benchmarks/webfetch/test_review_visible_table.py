"""Visible rows qualify without graphical accessibility labels."""
import pytest
from lexbench_webfetch import benchmark
from lexbench_webfetch.composite_tasks import evaluate_composite_acceptance


def contract():
    path = benchmark.SUITES / 'annotation-revisions/review-550-20261023.json'
    rows = benchmark.read(path)['changes']
    return next(r['set']['composite_acceptance'] for r in rows
                if r['case_id'] == 'dynamic-043-en-stable-grommet-table')


@pytest.mark.parametrize('names,passed', [
    (['Alan', 'Bryan', 'Chris', 'Eric'], True),
    (['New one', 'New two', 'New three', 'New four'], True),
    (['Alan', 'Bryan', 'Chris'], False),
    (['Alan'] * 4, False),
    ([''] * 4, False),
])
def test_real_distinct_visible_rows(names, passed):
    text = '<table><tr><th>Name</th><th>Complete</th></tr>' + ''.join(
        '<tr><td>'+name+'</td><td><svg aria-label="Not visible"></svg></td></tr>' for name in names) + '</table>'
    result = evaluate_composite_acceptance(text, contract(), base_url='https://example.test')
    assert result['composite_acceptance_pass'] is passed


def test_plain_markdown_without_graphics_passes():
    text = '| Name | Complete |\n| --- | --- |\n' + ''.join(
        '| '+n+' | |\n' for n in ['Alan', 'Bryan', 'Chris', 'Eric'])
    assert evaluate_composite_acceptance(text, contract(), base_url='https://example.test')['composite_acceptance_pass']
