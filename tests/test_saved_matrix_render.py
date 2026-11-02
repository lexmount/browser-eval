"""The multi-suite adapter must use the established full report owner."""
import json
import hashlib
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import pytest
from bs4 import BeautifulSoup
from tests.test_saved_matrix_evidence import fixture, build, write


@pytest.mark.parametrize('tie', [False, True])
def test_saved_suite_pass_rate_highlights_only_a_unique_winner(tmp_path, tie):
    spec, source, record, raw = fixture(tmp_path)
    other = deepcopy(record)
    other['provider'] = 'other'
    other['attempt_id'] = 'other-run'
    other['metrics']['content_pass'] = tie
    spec['providers'].append({'id': 'other', 'label': 'Other'})
    path = source/'results.jsonl'
    path.write_bytes(raw+(json.dumps(other)+'\n').encode())
    spec['sources'][0]['results']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    root = build(tmp_path, spec).parent
    home = BeautifulSoup((root/'index.html').read_text(), 'html.parser')
    table = home.select_one('#ranking-first')
    assert table is not None
    winners = table.select('[data-metric="task_pass_rate"] .comparison-winner')
    assert len(winners) == (0 if tie else 1)
    if not tie:
        assert '1/1' in winners[0].get_text()


def test_task_families_combine_suites_without_combining_case_identities(tmp_path):
    spec, _, _, raw = fixture(tmp_path)
    spec['suites'].append({**spec['suites'][0], 'id': 'second', 'label': 'Second'})
    spec['sources'].append({**spec['sources'][0], 'suite': 'second'})
    spec['report_families'] = [{'id': 'web', 'label': 'Static websites'}]
    with patch('harness.web_fetch.report.evaluate_body_requirements', side_effect=AssertionError('No rescoring')):
        root = build(tmp_path, spec).parent
    dataset = json.loads((root/'dataset.json').read_text())
    assert dataset['report_suites'] == [{'id': 'web', 'label': 'Static websites',
                                       'case_ids': ['first--same', 'second--same']}]
    home = BeautifulSoup((root/'index.html').read_text(), 'html.parser')
    assert len(home.select('#home-rankings [role="tabpanel"]')) == 1
    assert home.select_one('#ranking-web')
    assert 'Static websites' in home.get_text()
    for suite in ('first', 'second'):
        assert (root/f'evidence/{suite}/method/same/record.json').read_bytes() == raw
    inventory = BeautifulSoup((root/'datasets/index.html').read_text(), 'html.parser')
    assert {r['data-track'] for r in inventory.select('tr[data-track]')} == {'web'}


@pytest.mark.parametrize('families', [[], [{'id':'dynamic','label':'Wrong'}],
    [{'id':'web','label':'Static'}, {'id':'web','label':'Duplicate'}],
    [{'id':'web','label':'Static'}, {'id':'dynamic','label':'Unused'}],
    [{'id':'web','label':''}]])
def test_invalid_task_family_inventory_is_rejected(tmp_path, families):
    spec, _, _, _ = fixture(tmp_path)
    spec['report_families'] = families
    with pytest.raises(ValueError, match='registry|task family|partition'):
        build(tmp_path, spec)


def test_task_families_split_a_mixed_input_suite(tmp_path):
    spec, source, record, raw = fixture(tmp_path)
    path = Path(spec['suites'][0]['dataset']['path'])
    dataset = json.loads(path.read_text())
    dataset['cases'].append({**dataset['cases'][0], 'id': 'picture', 'track': 'image'})
    spec['suites'][0]['dataset'] = write(path, dataset)
    second = {**record, 'case_id': 'picture', 'attempt_id': 'run-picture'}
    results = source/'results.jsonl'
    results.write_bytes(raw + (json.dumps(second)+'\n').encode())
    spec['sources'][0]['results']['sha256'] = hashlib.sha256(results.read_bytes()).hexdigest()
    spec['sources'][0]['case_ids'].append('picture')
    spec['report_families'] = [{'id':'web','label':'Static'}, {'id':'web_media','label':'Embedded media'}]
    root = build(tmp_path, spec).parent
    projection = json.loads((root/'dataset.json').read_text())
    assert [(g['id'], g['case_ids']) for g in projection['report_suites']] == [
        ('web', ['first--same']), ('web_media', ['first--picture'])]
    home = BeautifulSoup((root/'index.html').read_text(), 'html.parser')
    assert home.select_one('#ranking-web') and home.select_one('#ranking-web_media')
    assert json.loads((root/'matrix.json').read_text())['totals']['passed'] == 2


def test_source_scoped_families_preserve_independent_populations(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['suites'].append({**spec['suites'][0], 'id':'second', 'label':'Second'})
    spec['sources'].append({**spec['sources'][0], 'suite':'second'})
    spec['report_families'] = [
        {'id':'web','label':'Main population','source_suites':['second']},
        {'id':'base-web','task_family':'web','label':'Base supplement','source_suites':['first']}]
    spec['combined_ranking'] = False
    root = build(tmp_path, spec).parent
    dataset = json.loads((root/'dataset.json').read_text())
    assert [(g['id'], g['case_ids']) for g in dataset['report_suites']] == [
        ('web', ['second--same']), ('base-web', ['first--same'])]
    home = BeautifulSoup((root/'index.html').read_text(),'html.parser')
    headers = [h.get_text() for h in home.select('#ranking-overall th')]
    assert any('Main population' in h for h in headers)
    assert any('Base supplement' in h for h in headers)
    assert not any('综合任务通过率' in h or '序号' in h for h in headers)
    assert '不将不同组直接合成通过率总榜' in home.get_text()
    assert '按任务类别分别比较' in home.get_text()
    assert '按本轮任务通过率排序' not in home.get_text()


@pytest.mark.parametrize('extra', [
    {'id':'second','task_family':'web','label':'Ambiguous'},
    {'id':'second','task_family':'web','label':'Unknown source','source_suites':['unknown']}])
def test_ambiguous_or_unknown_source_scoped_families_are_rejected(tmp_path, extra):
    spec, _, _, _ = fixture(tmp_path)
    spec['report_families'] = [{'id':'web','label':'Static'}, extra]
    with pytest.raises(ValueError, match='partition|source suites'):
        build(tmp_path, spec)


def test_original_template_and_immutable_scores(tmp_path):
    spec, source, raw_record, raw_bytes = fixture(tmp_path)
    spec['suites'].append({**spec['suites'][0], 'id':'second', 'label':'Second'})
    spec['sources'].append({**spec['sources'][0], 'suite':'second'})
    with patch('harness.web_fetch.report.evaluate_body_requirements', side_effect=AssertionError('Rescoring forbidden')):
        entry = build(tmp_path, spec)
    root = entry.parent
    soup = BeautifulSoup(entry.read_text(), 'html.parser')
    assert soup.select_one('#ranking-overall')
    assert soup.select_one('#saved-completion')
    assert len(soup.select('#saved-completion tbody tr')) == 1
    assert '调用完整：' not in soup.get_text()
    assert (root/'datasets/first--same.html').is_file()
    assert (root/'datasets/second--same.html').is_file()
    page = BeautifulSoup((root/'attempts/first--run-001.html').read_text(),'html.parser')
    assert page.select_one('#evidence-reference')
    assert page.select_one('#evidence-output')
    for row in (root/'results.jsonl').read_text().splitlines():
        assert json.loads(row)['metrics'] == raw_record['metrics']
    assert (source/'results.jsonl').read_bytes() == raw_bytes


def test_missing_does_not_become_scored_failure(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    case = {'id':'missing','name':'Missing','category':'test','url':'https://example.test/missing','expected_any':['answer']}
    dataset=json.loads(Path(spec['suites'][0]['dataset']['path']).read_text())
    dataset['cases'].append(case)
    spec['suites'][0]['dataset']=write(Path(spec['suites'][0]['dataset']['path']),dataset)
    receipt=write(tmp_path/'receipt.json',{'blocked_cases':[{'suite':'first','provider':'method','case_id':'missing', 'state':'pre_call_blocked_no_method_calls', 'native_route_receipt':{'transport_state':'not_started'}}]})
    spec['missing']=[{'suite':'first','provider':'method','case_id':'missing','status':'not_called','reason':'Pre-call block','receipt':receipt}]
    root=build(tmp_path,spec).parent
    soup=BeautifulSoup((root/'index.html').read_text(),'html.parser')
    assert len(soup.select('#saved-missing tbody tr')) == 1
    assert 'Pre-call block' in soup.get_text()
    assert len((root/'results.jsonl').read_text().splitlines()) == 1
    assert '未入库或未调用' in soup.get_text()


def test_five_suite_inventory_drives_rankings_and_filters(tmp_path):
    from harness.web_fetch.report import active_tracks
    spec, _, _, raw = fixture(tmp_path)
    original_suite, original_source = spec['suites'][0], spec['sources'][0]
    names=[('webfetch','综合 Web Fetch'),('dynamic-content','动态内容'),('popular-sites','热门网站'),('access-restrictions','访问限制'),('articles','文章')]
    spec['suites']=[{**original_suite,'id':key,'label':label} for key,label in names]
    spec['sources']=[{**original_source,'suite':key} for key,_ in names]
    root=build(tmp_path,spec).parent
    home=BeautifulSoup((root/'index.html').read_text(),'html.parser')
    assert len(home.select('#home-rankings [role="tabpanel"]'))==5
    for key,label in names:
        panel=home.select_one('#home-rankings-'+key)
        assert panel is not None
        assert label in panel.get_text()
        assert panel.select_one('#ranking-'+key)
        assert (root/f'evidence/{key}/method/same/record.json').read_bytes()==raw
    dataset=BeautifulSoup((root/'datasets/index.html').read_text(),'html.parser')
    assert {x.get('data-track') for x in dataset.select('tr[data-track]')}=={key for key,_ in names}
    provider=BeautifulSoup((root/'providers/method.html').read_text(),'html.parser')
    assert {x.get('data-track') for x in provider.select('tr[data-track]')}=={key for key,_ in names}
    projection=json.loads((root/'dataset.json').read_text())
    assert all(c.get('track','web')=='web' for c in projection['cases'])
    assert active_tracks({'x':{'id':'x','track':'web'}})==[('web','静态网站正文')]


def test_precall_rejection_preserves_score_but_excludes_product_failure(tmp_path):
    spec, source, original, _ = fixture(tmp_path)
    dataset_path = Path(spec['suites'][0]['dataset']['path'])
    dataset = json.loads(dataset_path.read_text())
    records = [original]
    for case_id, state in [('rejected', 'not_started'), ('failed', 'started')]:
        dataset['cases'].append({**dataset['cases'][0], 'id': case_id})
        record = deepcopy(original)
        record.update(case_id=case_id, attempt_id=case_id, ok=False,
                      elapsed_ms=9000 if state == 'not_started' else 24,
                      error_type='unsupported_task' if state == 'not_started' else 'network',
                      error='Synthetic failure')
        record['metrics'].update(content_pass=False, request_ok=False)
        record['provider_metadata']['native_route_receipt']['transport_state'] = state
        records.append(record)
    spec['suites'][0]['dataset'] = write(dataset_path, dataset)
    raw = ''.join(json.dumps(r)+'\n' for r in records).encode()
    (source/'results.jsonl').write_bytes(raw)
    spec['sources'][0]['results']['sha256'] = hashlib.sha256(raw).hexdigest()
    spec['sources'][0]['case_ids'] = ['same', 'rejected', 'failed']
    with patch('harness.web_fetch.report.evaluate_body_requirements', side_effect=AssertionError('No rescoring')):
        root = build(tmp_path, spec).parent
    matrix = json.loads((root/'matrix.json').read_text())
    assert matrix['totals']['failed'] == 1
    assert matrix['totals']['adapter_rejected'] == 1
    assert matrix['totals']['saved'] == 3
    projected = [json.loads(r) for r in (root/'results.jsonl').read_text().splitlines()]
    assert [r['metrics'] for r in projected] == [r['metrics'] for r in records]
    assert (source/'results.jsonl').read_bytes() == raw
    home = BeautifulSoup((root/'index.html').read_text(), 'html.parser')
    assert '1/2' in home.select_one('#ranking-overall').get_text()
    assert '1/2' in home.select_one('#ranking-first').get_text()
    suite_cells = home.select('#ranking-first tbody tr td')
    assert [c.get_text() for c in suite_cells[2:5]] == ['3', '3', '1']
    overall = home.select_one('#ranking-overall')
    assert '无法判定（含未调用）' in overall.get_text()
    timing_cells = overall.select('tbody tr td')[-3:]
    assert timing_cells[0].get_text() == '18 ms'
    assert '9,000' not in ''.join(c.get_text() for c in timing_cells)
    assert '未调用·适配器提前拒绝' in home.select_one('#saved-missing').get_text()
    provider = BeautifulSoup((root/'providers/method.html').read_text(), 'html.parser')
    assert '未调用·适配器提前拒绝' in provider.get_text()
    rejected_row = provider.select_one('tr[data-result="unmeasured"]')
    assert rejected_row['data-transport'] == 'na'
    assert rejected_row['data-content'] == 'na'
    assert len(provider.select('tr[data-result="fail"]')) == 1
    attempt = BeautifulSoup((root/'attempts/first--rejected.html').read_text(), 'html.parser')
    assert attempt.select_one('.status-line').get_text() == '未调用·适配器提前拒绝'
    assert '不计入通过率分母' in attempt.get_text()
    failed = BeautifulSoup((root/'attempts/first--failed.html').read_text(), 'html.parser')
    assert failed.select_one('.status-line').get_text() == '未通过'


def test_only_precall_rejections_have_no_latency_samples():
    from harness.web_fetch.report import overall_ranking
    record = {'case_id': 'case', 'provider': 'method', 'ok': False,
              'elapsed_ms': 9000, 'metrics': {'content_pass': False},
              'report_invocation_status': 'adapter_rejected'}
    soup = BeautifulSoup(overall_ranking([record], {'case': {'id': 'case'}}), 'html.parser')
    assert [c.get_text() for c in soup.select('#ranking-overall tbody td')[-3:]] == ['—', '—', '—']


def test_instance_method_labels_reach_full_renderer_and_reset(tmp_path):
    from harness.web_fetch.report import provider_label
    original = provider_label('method')
    spec, _, _, _ = fixture(tmp_path)
    label = 'Moli 1.1.19 · 系统 DNS'
    spec['providers'][0]['label'] = label
    root = build(tmp_path, spec).parent
    for path in ['index.html','providers/method.html','attempts/first--run-001.html','analysis/index.html']:
        assert label in BeautifulSoup((root/path).read_text(),'html.parser').get_text()
    assert provider_label('method') == original


def test_saved_scope_banner_distinguishes_uncommitted_and_user_skip(tmp_path):
    spec, source, _, _ = fixture(tmp_path)
    dataset_path=Path(spec['suites'][0]['dataset']['path'])
    dataset=json.loads(dataset_path.read_text())
    dataset['cases'] += [{**dataset['cases'][0],'id':'uncommitted'},
                         {**dataset['cases'][0],'id':'skipped'}]
    spec['suites'][0]['dataset']=write(dataset_path,dataset)
    called_receipt=write(tmp_path/'called.json', {'suite':'first','provider':'method',
        'case_id':'uncommitted','state':'called_but_uncommitted'})
    skip_receipt=write(tmp_path/'skip.json', {'kind':'user_requested_case_skip',
        'suite':'first','providers':['method'],'case_id':'skipped','status':'not_called',
        'score':None,'user_instruction':'Skip this case.'})
    spec['missing']=[
        {'suite':'first','provider':'method','case_id':'uncommitted','status':'uncommitted',
         'reason':'Call completed but persistence failed','receipt':called_receipt},
        {'suite':'first','provider':'method','case_id':'skipped','status':'not_called',
         'reason':'User requested skip','receipt':skip_receipt}]
    root=build(tmp_path,spec).parent
    for relative in ['index.html','providers/method.html','attempts/first--run-001.html']:
        page=BeautifulSoup((root/relative).read_text(),'html.parser')
        banner=page.select_one('[data-saved-completion]')
        assert banner is not None
        text=banner.get_text()
        assert '计划 3 项，已入库 1 项' in text
        assert '已调用未入库 1 项' in text
        assert '未调用（含按要求跳过）1 项' in text
        assert '缺少 2 个计划调用' not in page.get_text()


def test_saved_matrix_timing_labels_match_final_call_values(tmp_path):
    from harness.web_fetch.report import overall_ranking
    spec, _, record, _ = fixture(tmp_path)
    root=build(tmp_path,spec).parent
    page=BeautifulSoup((root/'index.html').read_text(),'html.parser')
    heads=[x.get_text().removesuffix(' ↕') for x in page.select('#ranking-overall th')]
    assert heads[-3:]==['最终调用耗时均值','最终调用耗时 P50','最终调用耗时 P95']
    values=[x.get_text() for x in page.select('#ranking-overall tbody tr td')][-3:]
    assert values==['12 ms','12 ms','12 ms']
    assert '每个逻辑结果只取最终一次调用耗时' in page.get_text()
    assert '总耗时按每题所有物理调用累计' not in page.get_text()
    legacy=BeautifulSoup(overall_ranking([record],{'same':{'id':'same'}}),'html.parser')
    assert [x.get_text().removesuffix(' ↕') for x in legacy.select('th')][-3:]==['最终调用耗时均值','最终调用耗时 P50','最终调用耗时 P95']
    assert '总耗时按每题所有物理调用累计' not in legacy.get_text()
    assert [x.get_text() for x in legacy.select('tbody tr td')][-3:]==values


def test_entirely_missing_method_stays_in_both_rankings(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['providers'].append({'id': 'absent', 'label': 'Absent method'})
    receipt = write(tmp_path/'absent.json', {'suite': 'first', 'provider': 'absent',
        'case_id': 'same', 'state': 'pre_call_blocked_no_method_calls',
        'native_route_receipt': {'transport_state': 'not_started'}})
    spec['missing'] = [{'suite': 'first', 'provider': 'absent', 'case_id': 'same',
        'status': 'not_called', 'reason': 'Pre-call block', 'receipt': receipt}]
    root = build(tmp_path, spec).parent
    home = BeautifulSoup((root/'index.html').read_text(), 'html.parser')
    for selector in ['#ranking-overall', '#ranking-first']:
        rows = home.select(selector+' tbody tr')
        assert len(rows) == 2
        absent = next(row for row in rows if 'Absent method' in row.get_text())
        assert '未完成' in absent.get_text()
    assert (root/'providers/absent.html').is_file()
    assert '2 种方法' in home.get_text()
    assert '调用矩阵尚不完整，暂不汇总全部方法共同成功任务的耗时。' in home.get_text()
    assert '全部 1 种受测配置共同通过' not in home.get_text()


def test_nested_suite_names_partition_by_identity(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['suites'].append({**spec['suites'][0], 'id': 'first--nested'})
    spec['sources'].append({**spec['sources'][0], 'suite': 'first--nested'})
    root = build(tmp_path, spec).parent
    dataset = json.loads((root/'dataset.json').read_text())
    assert [s['case_ids'] for s in dataset['report_suites']] == [
        ['first--same'], ['first--nested--same']]


def test_suite_ranking_orders_by_fraction_and_exposes_numeric_sort():
    from harness.web_fetch.report import saved_suite_ranking
    cases = {'a': {'id': 'a', 'report_family': 'suite'},
             'b': {'id': 'b', 'report_family': 'suite'}}
    records = [dict(provider=provider, case_id=cid, ok=True,
                    metrics={'content_pass': passed})
               for provider, cid, passed in [('alphabetic-first','a',True),
                   ('alphabetic-first','b',False), ('z-winner','a',True),
                   ('z-winner','b',True)]]
    soup = BeautifulSoup(saved_suite_ranking(records,cases,'suite'),'html.parser')
    rows = soup.select('tbody tr')
    assert 'z-winner' in rows[0].get_text()
    assert [r.select('td')[-1]['data-sort'] for r in rows] == ['1.0','0.5']
    assert len(soup.select('thead button')) == 7
    for row in rows:
        assert all(cell['data-sort'] == cell.get_text() for cell in row.select('td')[2:7])


def test_final_call_timing_labels_are_localized_in_ordinary_reports():
    from pathlib import PurePosixPath
    from harness.web_fetch.report import overall_ranking
    from harness.web_fetch.report_i18n import localize_html
    record = {'provider': 'method', 'case_id': 'case', 'ok': True,
              'elapsed_ms': 10, 'metrics': {'content_pass': True}}
    page = localize_html(overall_ranking([record], {'case': {'id': 'case'}}), PurePosixPath('index.html'))
    assert 'Mean final-call latency' in page
    assert 'P50 final-call latency' in page
    assert '最终调用耗时' not in page
    from harness.web_fetch.report_i18n import SAVED_MATRIX_ZH, TEXT
    for key in ('final_call_timing_title', 'final_call_timing_help', 'final_call_timing_note'):
        assert SAVED_MATRIX_ZH[key] not in page
        assert TEXT[SAVED_MATRIX_ZH[key]] in page
    assert 'P95 final-call latency' in page


def test_ordinary_english_generation_localizes_all_final_call_timing_text(tmp_path):
    from harness.web_fetch.report import generate
    from harness.web_fetch.report_i18n import SAVED_MATRIX_ZH, TEXT
    _, source, record, _ = fixture(tmp_path)
    dataset = json.loads((source/'dataset.json').read_text())
    generate(source, dataset, [record], [], include_english=True,
             authored_interpretation=False, frozen_scores=True)
    page = BeautifulSoup((source/'en/index.html').read_text(), 'html.parser')
    overview = page.select_one('#overall-ranking').get_text(' ', strip=True)
    strings = [*SAVED_MATRIX_ZH['final_call_timing_headers'],
               *(SAVED_MATRIX_ZH[key] for key in
                 ('final_call_timing_title', 'final_call_timing_help', 'final_call_timing_note'))]
    for original in strings:
        assert original not in overview
        assert TEXT[original] in overview


@pytest.mark.parametrize('excluded', [['base-web'], ['unknown'], ['web', 'base-web'], ['base-web', 'base-web']])
def test_excluded_report_families_preserve_sources_and_recompute_population(tmp_path, excluded):
    spec, source, _, original = fixture(tmp_path)
    spec['suites'].append({**spec['suites'][0], 'id': 'second', 'label': 'Second'})
    spec['sources'].append({**spec['sources'][0], 'suite': 'second'})
    spec['report_families'] = [
        {'id': 'web', 'label': 'Main population', 'source_suites': ['second']},
        {'id': 'base-web', 'task_family': 'web', 'label': 'Base supplement', 'source_suites': ['first']}]
    spec['excluded_report_families'] = excluded
    if excluded != ['base-web']:
        with pytest.raises(ValueError, match='excluded report families'):
            build(tmp_path, spec)
        return
    root = build(tmp_path, spec).parent
    model = json.loads((root/'matrix.json').read_text())
    assert len(model['rows']) == model['totals']['saved'] == 1
    assert model['rows'][0]['suite'] == 'second'
    assert model['report_scope']['source_positions'] == 2
    assert (source/'results.jsonl').read_bytes() == original
    assert not (root/'evidence/first').exists()
    home = BeautifulSoup((root/'index.html').read_text(), 'html.parser')
    assert 'Base supplement' not in home.get_text()
    assert 'Main population' in home.get_text()
    dataset = json.loads((root/'dataset.json').read_text())
    assert [case['id'] for case in dataset['cases']] == ['second--same']
