"""The multi-suite adapter must use the established full report owner."""
import json
import hashlib
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
from tests.test_saved_matrix_evidence import fixture, build, write


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
    receipt=write(tmp_path/'receipt.json',{'blocked_cases':[{'suite':'first','case_id':'missing','error':'Pre-call block'}]})
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
    assert [x.get_text().removesuffix(' ↕') for x in legacy.select('th')][-3:]==['总耗时均值','总耗时 P50','总耗时 P95']
    assert '总耗时按每题所有物理调用累计' in legacy.get_text()
    assert [x.get_text() for x in legacy.select('tbody tr td')][-3:]==values
