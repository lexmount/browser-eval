"""Rank only comparable saved populations and retain exact score ties."""
from copy import deepcopy

from bs4 import BeautifulSoup
import pytest

from harness.web_fetch.report import overall_ranking, ranking
from tests.test_report_body_scope import case, record


@pytest.mark.parametrize('render', ['overall', 'family'])
@pytest.mark.parametrize('problem', ['incomplete', 'mixed', 'missing', 'diagnostic', 'duplicate', 'tie'])
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
    elif problem == 'duplicate':
        records.extend(deepcopy(r) for r in records[:1]+records[2:3])
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


@pytest.mark.parametrize('missing', [False, True])
def test_english_home_explains_unknown_and_missing_results(tmp_path, monkeypatch, missing):
    from harness.web_fetch import report
    monkeypatch.setattr(report, 'write_support', lambda *args, **kwargs: None)
    monkeypatch.setattr('harness.web_fetch.report_explainer.render_explainer', lambda *args, **kwargs: 'article')
    cases = [case(str(i), kind='body', track='web') for i in range(2)]
    records = [record(c) for c in cases]
    records[0]['metrics']['acceptance_checks'] = {'content': None}
    if missing:
        records.pop()
    for row in records:
        artifact = tmp_path/row['artifact_path']
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('output', encoding='utf-8')
    report.generate(tmp_path, {'cases': cases}, records, [], include_english=True)
    home = (tmp_path/'en/index.html').read_text(encoding='utf-8')
    assert '1 task-family overview' in home
    assert '1 family rankings' in home
    assert '2 cases · 1 measured methods' in home
    assert '类任务概览' not in home and '种已测方法' not in home
    if missing:
        assert 'Some results are missing or tasks were not called.' in home
    else:
        assert 'Decidable denominators differ' in home
