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
    if selection == 'diagnostic':
        from pathlib import PurePosixPath
        from harness.web_fetch.report_i18n import localize_html
        assert '诊断表按任务通过率排列，便于查看，不计算名次。' in soup.get_text()
        assert '名次按本次保存结果' not in soup.get_text()
        english = localize_html(str(soup), PurePosixPath('index.html'))
        assert 'Diagnostic rows are sorted by task pass rate for inspection; no ranks are assigned.' in english
    else:
        assert '诊断表按任务通过率排列' not in soup.get_text()
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


@pytest.mark.parametrize('declared_missing', [False, True])
def test_family_participants_do_not_include_other_tracks(declared_missing):
    from harness.web_fetch import report
    web = case('web', kind='body', track='web')
    dynamic = case('dynamic', kind='body', track='dynamic')
    first, other = record(web), record(dynamic)
    first['provider'], other['provider'] = 'web-only', 'dynamic-only'
    plan = {'web': ['web-only'] + (['missing-web'] if declared_missing else []),
            'dynamic': ['dynamic-only']}
    token = report._RENDER_CONTEXT.set(report.RenderContext(coverage_plan=plan))
    try:
        markup = report.ranking([first, other], {'web': web, 'dynamic': dynamic}, 'web')
        card = report.overview_card([first, other], {'web': web, 'dynamic': dynamic},
                                    'web', 'Web', ['web-only', 'dynamic-only'])
    finally:
        report._RENDER_CONTEXT.reset(token)
    soup = BeautifulSoup(markup, 'html.parser')
    assert bool(soup.select('td[data-rank]')) is (not declared_missing)
    assert ('仅供诊断' in card) is declared_missing


def test_overall_ranks_require_even_wholly_missing_declared_methods():
    from harness.web_fetch import report
    c = case('example', kind='body', track='web')
    row = record(c)
    token = report._RENDER_CONTEXT.set(report.RenderContext(
        coverage_plan={'web': ['fixture', 'missing-method']}))
    try:
        markup = report.overall_ranking([row], {c['id']: c})
    finally:
        report._RENDER_CONTEXT.reset(token)
    soup = BeautifulSoup(markup, 'html.parser')
    assert not soup.select('td[data-rank]')
    assert not soup.select('#ranking-overall .comparison-winner')
    assert '结果未覆盖同一组完整任务' in soup.get_text()


def test_overview_and_rankings_exclude_the_same_tasks():
    from harness.web_fetch.report import overview_card

    included, excluded = (case(name, kind='body', track='web') for name in ('included', 'excluded'))
    cases = {item['id']: item for item in (included, excluded)}
    excluded_row = record(excluded)
    excluded_row['metrics']['evaluation_excluded'] = True
    records = [record(included), excluded_row]
    assert BeautifulSoup(ranking(records, cases, 'web'), 'html.parser').select_one('td[data-rank]')
    assert BeautifulSoup(overall_ranking(records, cases), 'html.parser').select_one('td[data-rank]')
    assert overview_card(records, cases, 'web', 'Web', ['fixture']) == overview_card(
        records[:1], {'included': included}, 'web', 'Web', ['fixture'])
