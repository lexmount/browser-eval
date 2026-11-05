"""Rank only comparable saved populations and retain exact score ties."""
from copy import deepcopy

from bs4 import BeautifulSoup
import pytest

from harness.web_fetch.report import overall_ranking, ranking
from tests.test_report_body_scope import case, record


@pytest.mark.parametrize('render', ['overall', 'family'])
@pytest.mark.parametrize('problem', ['incomplete', 'mixed', 'missing', 'diagnostic', 'tie'])
def test_ranking_admission_and_ties(render, problem):
    cases = [case(str(i), kind='body', track='web') for i in range(2)]
    records = []
    for name in ['a', 'b']:
        for c in cases:
            row = record(c)
            row['provider'] = name
            records.append(row)
    if problem == 'incomplete':
        records.pop()
    elif problem == 'mixed':
        records[-1]['metrics']['scoring_source_sha256'] = 'b' * 64
    elif problem == 'missing':
        records[-1]['metrics'].pop('scoring_source_sha256')
    frozen = deepcopy(records)
    args = [records, {c['id']: c for c in cases}]
    if render == 'family':
        args.append('web')
    markup = (ranking if render == 'family' else overall_ranking)(*args, diagnostic=problem == 'diagnostic')
    soup = BeautifulSoup(markup, 'html.parser')
    table = soup.select_one('table.ranking')
    if problem == 'tie':
        assert [r.select_one('td').get_text() for r in table.select('tbody tr')] == ['1', '1']
        assert len(table.select('td[data-rank="1"]')) == 2
    else:
        assert table.get('data-row-index') == 'false'
        assert not table.select('.comparison-winner')
        assert not table.select('td[data-rank]')
    assert records == frozen


@pytest.mark.parametrize('selection', ['diagnostic', 'saved_task_pass_rate'])
@pytest.mark.parametrize('mixed', [False, True])
def test_saved_builder_ranks_require_selection_and_matching_scorer(tmp_path, selection, mixed):
    import hashlib
    import json
    from tests.test_saved_matrix_evidence import fixture, build
    from harness.web_fetch.report_saved_matrix import check_saved_matrix
    spec, source, row, raw = fixture(tmp_path)
    spec['ranking'] = selection
    second = deepcopy(row)
    second.update(provider='other', attempt_id='other-call')
    if mixed:
        second['metrics']['scoring_source_sha256'] = 'b' * 64
    spec['providers'].append({'id': 'other', 'label': 'Other'})
    raw += (json.dumps(second)+'\n').encode()
    (source/'results.jsonl').write_bytes(raw)
    spec['sources'][0]['results']['sha256'] = hashlib.sha256(raw).hexdigest()
    root = build(tmp_path, spec).parent
    assert check_saved_matrix(root, spec_path=tmp_path/'spec.json')['valid']
    soup = BeautifulSoup((root/'index.html').read_text(encoding='utf-8'), 'html.parser')
    for name in ['overall', 'first']:
        table = soup.select_one('#ranking-'+name)
        if selection == 'saved_task_pass_rate' and not mixed:
            assert [td.get_text() for td in table.select('td[data-rank]')] == ['1', '1']
        else:
            assert table.get('data-row-index') == 'false'
            assert not table.select('.comparison-winner')
    if mixed:
        assert '评分版本不同或缺失' in soup.get_text()
    assert (source/'results.jsonl').read_bytes() == raw
