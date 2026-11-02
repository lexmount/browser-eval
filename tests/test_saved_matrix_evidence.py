"""Immutable saved-matrix assembly and negative integrity controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture(tmp_path):
    case = {'id': 'same', 'name': 'Example', 'category': 'test', 'url': 'https://example.test/', 'expected_any': ['answer']}
    root = tmp_path / 'source'
    dataset = write(root / 'dataset.json', {'cases': [case]})
    answer = root / 'artifacts/answer.txt'
    answer.parent.mkdir(parents=True)
    answer.write_text('answer')
    record = {'attempt_id': 'run-001', 'started_at':'2026-10-29T00:00:00Z', 'provider_type':'test', 'case_id': 'same', 'provider': 'method', 'url': case['url'], 'ok': True,
              'elapsed_ms': 12.0, 'artifact_path': 'artifacts/answer.txt',
              'metrics': {'content_sha256': hashlib.sha256(answer.read_bytes()).hexdigest(),
                          'content_pass': True, 'request_ok': True},
              'provider_metadata': {'native_route_receipt': {'transport_state': 'started'}}}
    result = root / 'results.jsonl'
    raw = (json.dumps(record, separators=(',', ':')) + '\n').encode()
    result.write_bytes(raw)
    spec = {'schema_version': 1, 'title': 'Saved evidence', 'providers': [{'id': 'method', 'label': 'Method'}],
            'suites': [{'id': 'first', 'label': 'First', 'dataset': dataset}],
            'sources': [{'suite': 'first', 'root': str(root),
                         'results': {'path': str(result), 'sha256': hashlib.sha256(raw).hexdigest()},
                         'case_ids': ['same']}], 'missing': [], 'limitations': []}
    return spec, root, record, raw


def build(tmp_path, spec, name='report'):
    path = tmp_path / 'spec.json'
    write(path, spec)
    return build_saved_matrix(path, tmp_path / name)


def test_current_selection_omits_other_methods_and_superseded_payloads(tmp_path):
    spec, root, record, raw = fixture(tmp_path)
    excluded = deepcopy(record)
    excluded['provider'] = 'removed-method'
    excluded['artifact_path'] = 'artifacts/discarded.txt'
    (root / excluded['artifact_path']).write_text('discarded intermediate output')
    results = root / 'results.jsonl'
    results.write_bytes(raw + (json.dumps(excluded) + '\n').encode())
    spec['sources'][0].update(provider='method', results={
        'path': str(results), 'sha256': hashlib.sha256(results.read_bytes()).hexdigest()})
    report = build(tmp_path, spec).parent
    model = json.loads((report / 'matrix.json').read_text())
    assert len(model['rows']) == 1
    assert model['rows'][0]['provider'] == 'method'
    assert not model['rows'][0].get('history')
    assert not list(report.rglob('discarded.txt'))
    assert not list(report.rglob('history'))
    assert (report / model['rows'][0]['record_path']).read_bytes() == raw
    assert (root / excluded['artifact_path']).is_file()


def test_preserves_bytes_and_qualified_same_id(tmp_path):
    spec, root, record, raw = fixture(tmp_path)
    spec['suites'].append({**spec['suites'][0], 'id': 'second', 'label': 'Second'})
    spec['sources'].append({**spec['sources'][0], 'suite': 'second'})
    entry = build(tmp_path, spec)
    assert entry.name == 'index.html'
    for suite in ['first', 'second']:
        assert (entry.parent / f'evidence/{suite}/method/same/record.json').read_bytes() == raw
    model = json.loads((entry.parent / 'matrix.json').read_text())
    assert model['totals']['saved'] == 2
    assert all(r['passed'] and r['normal'] for r in model['rows'])
    assert (root / 'results.jsonl').read_bytes() == raw
    assert check_saved_matrix(entry.parent)['valid']
    with pytest.raises(ValueError, match='new directory'):
        build_saved_matrix(tmp_path / 'spec.json', entry.parent)


def test_duplicate_result_and_unexplained_missing_rejected(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] *= 2
    with pytest.raises(ValueError, match='Duplicate'):
        build(tmp_path, spec)
    spec['sources'] = []
    with pytest.raises(ValueError, match='Unexplained missing'):
        build(tmp_path, spec)


def test_hash_and_answer_tampering_rejected(tmp_path):
    spec, root, _, _ = fixture(tmp_path)
    spec['sources'][0]['results']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash differs'):
        build(tmp_path, spec)
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((root / 'results.jsonl').read_bytes()).hexdigest()
    (root / 'artifacts/answer.txt').write_text('altered')
    with pytest.raises(ValueError, match='binding'):
        build(tmp_path, spec)


def test_receipt_bound_missing_not_a_failure(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] = []
    receipt = write(tmp_path / 'receipt.json', {'blocked_cases': [
        {'suite': 'first', 'provider': 'method', 'case_id': 'same',
         'state': 'pre_call_blocked_no_method_calls',
         'native_route_receipt': {'transport_state': 'not_started'}}]})
    spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                        'status': 'not_called', 'reason': 'No contract', 'receipt': receipt}]
    entry = build(tmp_path, spec)
    row = json.loads((entry.parent / 'matrix.json').read_text())['rows'][0]
    assert row['passed'] is None and row['called'] is False and row['record_path'] is None
    changed = deepcopy(spec)
    changed['missing'][0]['status'] = 'uncommitted'
    with pytest.raises(ValueError, match='receipt does not identify'):
        build(tmp_path, changed, 'other')


def test_checker_rejects_changed_original_and_portable_bytes(tmp_path):
    spec, root, _, raw = fixture(tmp_path)
    entry = build(tmp_path, spec)
    (root / 'results.jsonl').write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='source changed'):
        check_saved_matrix(entry.parent, spec_path=tmp_path / 'spec.json')
    (root / 'results.jsonl').write_bytes(raw)
    (entry.parent / 'index.html').write_text('broken')
    with pytest.raises(ValueError, match='Portable'):
        check_saved_matrix(entry.parent, spec_path=tmp_path / 'spec.json')


def test_unknown_selection_and_symlink_rejected(tmp_path):
    spec, root, _, _ = fixture(tmp_path)
    spec['sources'][0]['case_ids'] = ['unknown']
    with pytest.raises(ValueError, match='Unknown'):
        build(tmp_path, spec)
    spec['sources'][0]['case_ids'] = ['same']
    link = root / 'alias.jsonl'
    link.symlink_to(root / 'results.jsonl')
    spec['sources'][0]['results']['path'] = str(link)
    with pytest.raises(ValueError, match='Symlink'):
        build(tmp_path, spec)


@pytest.mark.parametrize('base', [None, '../'])
def test_nested_route_links_resolve_from_page_or_base(tmp_path, monkeypatch, base):
    from harness.web_fetch import report
    original = report.generate_saved_matrix

    def generate(out, model):
        entry = original(out, model)
        nested = out / 'providers' / 'example.html'
        nested.parent.mkdir(exist_ok=True)
        prefix = '' if base else '../'
        head = '<base href="../">' if base else ''
        nested.write_text(head + '<a href="' + prefix + 'index.html?view=all#top">Home</a>'
                          + '<a href="https://external.invalid/missing">External</a>'
                          + '<a href="' + prefix + model['rows'][0]['answer_path'] + '">Answer</a>')
        return entry

    monkeypatch.setattr(report, 'generate_saved_matrix', generate)
    spec, _, _, _ = fixture(tmp_path)
    entry = build(tmp_path, spec)
    assert check_saved_matrix(entry.parent)['html_routes_verified'] >= 2


@pytest.mark.parametrize('markup,message', [
    ('<a href="missing.html">Missing</a>', 'Broken saved-matrix static link'),
    ('<base href="../../"><a href="index.html">Home</a>', 'base escapes'),
    ('<base href="../"><a href="../outside.html">Outside</a>', 'static link escapes'),
])
def test_nested_route_invalid_target_rejected(tmp_path, monkeypatch, markup, message):
    from harness.web_fetch import report
    original = report.generate_saved_matrix

    def generate(out, model):
        entry = original(out, model)
        nested = out / 'providers' / 'example.html'
        nested.parent.mkdir(exist_ok=True)
        nested.write_text(markup)
        return entry

    monkeypatch.setattr(report, 'generate_saved_matrix', generate)
    spec, _, _, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match=message):
        build(tmp_path, spec)


def test_explicit_user_skip_is_unscored_and_provider_scoped(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] = []
    receipt = {'kind': 'user_requested_case_skip', 'suite': 'first', 'case_id': 'same',
               'providers': ['method'], 'status': 'not_called', 'score': None,
               'user_instruction': 'Skip this case.'}
    spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                        'status': 'not_called', 'reason': 'User requested skip',
                        'receipt': write(tmp_path/'skip.json', receipt)}]
    entry = build(tmp_path, spec)
    model = json.loads((entry.parent/'matrix.json').read_text())
    assert model['totals']['failed'] == model['totals']['called'] == 0
    assert model['rows'][0]['passed'] is None
    receipt['providers'] = ['different-method']
    spec['missing'][0]['receipt'] = write(tmp_path/'wrong-skip.json', receipt)
    with pytest.raises(ValueError, match='receipt does not identify'):
        build(tmp_path, spec, 'wrong-provider')


def test_superseded_evidence_preserved_without_double_counting(tmp_path):
    spec, root, record, raw = fixture(tmp_path)
    old = deepcopy(record)
    old.update(attempt_id='old-001', ok=False, error_type='unsupported_task')
    old['metrics'].update(content_pass=False, request_ok=False)
    old['provider_metadata']['native_route_receipt']['transport_state'] = 'not_started'
    old_raw = (json.dumps(old)+'\n').encode()
    old_results = root/'old-results.jsonl'
    old_results.write_bytes(old_raw)
    old_copy = root/'old-record.json'
    old_copy.write_bytes(old_raw)
    history_source = {**spec['sources'][0], 'results': {
        'path': str(old_results), 'sha256': hashlib.sha256(old_raw).hexdigest()}}
    spec['superseded_sources'] = [history_source]
    scope = {'kind':'predeclared_replacement_scope', 'created_at':'2026-10-28T00:00:00Z', 'cells': [{
        'suite':'first', 'provider':'method', 'case_id':'same', 'old_record': {
            'path':str(old_copy), 'sha256': hashlib.sha256(old_raw).hexdigest()}}]}
    spec['supersession_scope'] = write(tmp_path/'scope.json', scope)
    entry = build(tmp_path, spec)
    model = json.loads((entry.parent/'matrix.json').read_text())
    assert model['totals']['saved'] == model['totals']['passed'] == 1
    assert len((entry.parent/'results.jsonl').read_text().splitlines()) == 1
    history = model['rows'][0]['history'][0]
    assert (entry.parent/history['record_path']).read_bytes() == old_raw
    assert (entry.parent/'evidence/first/method/same/record.json').read_bytes() == raw
    public_scope = (entry.parent/history['scope_path']).read_text()
    assert str(tmp_path) not in public_scope
    assert 'superseded-history' in (entry.parent/'attempts/first--run-001.html').read_text()
    assert check_saved_matrix(entry.parent)['valid']
    for timestamp in (None, '2026-10-30T00:00:00Z'):
        invalid = deepcopy(spec)
        invalid['supersession_scope'] = write(tmp_path/'late-scope.json', {**scope, 'created_at': timestamp})
        with pytest.raises(ValueError, match='timestamp|predates'):
            build(tmp_path, invalid, 'late-scope-' + str(timestamp))
    spec['supersession_scope'] = write(tmp_path/'wrong-scope.json', {**scope,'cells':[]})
    with pytest.raises(ValueError, match='complete replacement scope'):
        build(tmp_path,spec,'invalid-scope')


def test_uncommitted_replacement_retains_old_history_without_score(tmp_path):
    spec, root, old, raw = fixture(tmp_path)
    spec['superseded_sources'] = spec.pop('sources')
    spec['sources'] = []
    old_file = root/'old-record.json'
    old_file.write_bytes(raw)
    spec['supersession_scope'] = write(tmp_path/'scope.json', {
        'kind':'predeclared_replacement_scope', 'created_at':'2026-10-28T00:00:00Z', 'cells':[{
            'suite':'first','provider':'method','case_id':'same',
            'old_record':{'path':str(old_file),'sha256':hashlib.sha256(raw).hexdigest()}}]})
    diagnostic = write(tmp_path/'diagnostic.json', {'observed_http_status':502})
    receipt = write(tmp_path/'receipt.json', {'suite':'first','provider':'method',
        'case_id':'same','state':'called_but_uncommitted','elapsed_ms':None,'started_at':'2026-10-29T00:00:00Z'})
    spec['missing'] = [{'suite':'first','provider':'method','case_id':'same',
        'status':'uncommitted','reason':'Collection error after HTTP 502',
        'receipt':receipt,'diagnostic_sources':[diagnostic]}]
    entry=build(tmp_path,spec)
    model=json.loads((entry.parent/'matrix.json').read_text())
    assert model['totals']['saved']==model['totals']['failed']==model['totals']['passed']==0
    assert model['totals']['called']==model['totals']['uncommitted']==1
    row=model['rows'][0]
    assert row['passed'] is None and row['elapsed_ms'] is None
    assert (entry.parent/row['history'][0]['record_path']).read_bytes()==raw
    assert row['history'][0]['record_path'] in (entry.parent/'index.html').read_text()
    assert check_saved_matrix(entry.parent)['valid']
    Path(diagnostic['path']).write_text('{}')
    with pytest.raises(ValueError,match='source changed'):
        check_saved_matrix(entry.parent, spec_path=tmp_path / 'spec.json')
    spec['missing'][0]['status']='not_called'
    spec['missing'][0]['receipt']=write(tmp_path/'uncalled.json',{
        'suite':'first','provider':'method','case_id':'same',
        'state':'pre_call_blocked_no_method_calls',
        'native_route_receipt':{'transport_state':'not_started'}})
    spec['missing'][0]['diagnostic_sources']=[]
    with pytest.raises(ValueError,match='current call or explicit uncommitted'):
        build(tmp_path,spec,'uninvoked-correction')

@pytest.mark.parametrize('final_pass,prior_pass', [(True, False), (False, False), (False, True)])
def test_retry_continuation_keeps_final_verdict_and_prior_bytes(tmp_path, final_pass, prior_pass):
    spec, root, current, raw = fixture(tmp_path)
    old = deepcopy(current)
    old['ok'] = prior_pass
    old['metrics'].update(content_pass=prior_pass, request_ok=prior_pass)
    old['elapsed_ms'] = 8.0
    old_raw = (json.dumps(old)+'\n').encode()
    old_results = root/'prior-results.jsonl'
    old_results.write_bytes(old_raw)
    current['ok'] = final_pass
    current['metrics'].update(content_pass=final_pass, request_ok=final_pass)
    current['started_at'] = '2026-10-29T02:00:00Z'
    raw = (json.dumps(current)+'\n').encode()
    (root/'results.jsonl').write_bytes(raw)
    spec['sources'][0]['results']['sha256'] = hashlib.sha256(raw).hexdigest()
    launch = write(tmp_path/'launch.json', {'started_at':'2026-10-29T01:00:00Z',
        'total_authorized_physical_calls':2, 'first_record':{'record':old}})
    prior_source = {**deepcopy(spec['sources'][0]), 'dataset':spec['suites'][0]['dataset'],
        'results':{'path':str(old_results),'sha256':hashlib.sha256(old_raw).hexdigest()}}
    spec['retry_continuations'] = [{'suite':'first','provider':'method','case_id':'same',
        'launch_contract':launch,'current_record_sha256':hashlib.sha256(raw).hexdigest(),
        'prior_source':prior_source}]
    spec['limitations'] = ['Synthetic retry disclosure.']
    if prior_pass:
        with pytest.raises(ValueError, match='failed first call'):
            build(tmp_path, spec)
        return
    entry=build(tmp_path,spec)
    row=json.loads((entry.parent/'matrix.json').read_text())['rows'][0]
    assert row['passed'] is final_pass and row['elapsed_ms']==12.0 and row['retries']==1
    continuation=row['retry_continuation']
    assert continuation['total_physical_calls']==2 and continuation['execution_cost_ms']==20.0
    assert (entry.parent/continuation['prior_record_path']).read_bytes()==old_raw
    assert (entry.parent/row['record_path']).read_bytes()==raw
    assert 'retry-continuation' in (entry.parent/'attempts/first--run-001.html').read_text()
    assert 'Synthetic retry disclosure.' in (entry.parent/'index.html').read_text()
    assert check_saved_matrix(entry.parent)['valid']
    for defect in ('prior_not_called', 'current_not_called', 'same_call', 'late_prior'):
        prior, final = deepcopy(old), deepcopy(current)
        if defect == 'prior_not_called':
            prior['provider_metadata']['native_route_receipt']['transport_state'] = 'not_started'
        elif defect == 'current_not_called':
            final['provider_metadata']['native_route_receipt']['transport_state'] = 'not_started'
        elif defect == 'same_call':
            prior = deepcopy(final)
        else:
            prior['started_at'] = '2026-10-29T03:00:00Z'
        old_results.write_text(json.dumps(prior)+'\n')
        (root/'results.jsonl').write_text(json.dumps(final)+'\n')
        invalid = deepcopy(spec)
        invalid['sources'][0]['results']['sha256'] = hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()
        continuation = invalid['retry_continuations'][0]
        continuation['current_record_sha256'] = invalid['sources'][0]['results']['sha256']
        continuation['prior_source']['results']['sha256'] = hashlib.sha256(old_results.read_bytes()).hexdigest()
        launch_data = json.loads(Path(launch['path']).read_text())
        launch_data['first_record']['record'] = prior
        continuation['launch_contract'] = write(tmp_path/(defect+'.json'), launch_data)
        with pytest.raises(ValueError):
            build(tmp_path, invalid, defect)
    old_results.write_bytes(old_raw)
    (root/'results.jsonl').write_bytes(raw)
    changed=deepcopy(spec)
    changed['retry_continuations'][0]['current_record_sha256']='0'*64
    with pytest.raises(ValueError,match='current record differs'):
        build(tmp_path,changed,'wrong-final')
    changed=deepcopy(spec)
    contract=json.loads(Path(launch['path']).read_text())
    contract['case_ids']=['same','missing'];contract['provider']='method'
    changed['retry_continuations'][0]['launch_contract']=write(tmp_path/'wider-scope.json',contract)
    with pytest.raises(ValueError,match='complete declared scope'):
        build(tmp_path,changed,'partial-continuation')


def test_retry_continuation_preserves_uncommitted_prior_without_invented_time(tmp_path):
    spec, root, current, raw = fixture(tmp_path)
    log=write(tmp_path/'first-log.jsonl', {'event':'http_request_started'})
    receipt=write(tmp_path/'uncommitted.json', {'suite':'first','provider':'method','case_id':'same',
        'state':'called_but_uncommitted','elapsed_ms':None})
    launch=write(tmp_path/'launch.json', {'started_at':'2026-10-28T00:00:00Z',
        'case_ids':['same'],'provider':'method','total_authorized_physical_calls_per_case':2,
        'first_uncommitted':{'case_id':'same','sha256':log['sha256']}})
    spec['retry_continuations']=[{'suite':'first','provider':'method','case_id':'same',
        'launch_contract':launch,'current_record_sha256':hashlib.sha256(raw).hexdigest(),
        'prior_uncommitted':receipt,'prior_log':log}]
    entry=build(tmp_path,spec)
    row=json.loads((entry.parent/'matrix.json').read_text())['rows'][0]
    assert row['passed'] is True and row['retry_continuation']['execution_cost_ms'] is None
    assert '首轮精确耗时未保存' in (entry.parent/'attempts/first--run-001.html').read_text()


@pytest.mark.parametrize('digest', [None, '', 'abc', 'g' * 64, 12])
def test_missing_or_invalid_hash_rejected(tmp_path, digest):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'][0]['results']['sha256'] = digest
    with pytest.raises(ValueError, match='SHA-256'):
        build(tmp_path, spec)


@pytest.mark.parametrize('receipt', [
    {'case_id': 'same', 'error': 'Timeout after request'},
    {'suite': 'first', 'case_id': 'same', 'state': 'pre_call_blocked_no_method_calls'},
    {'suite': 'first', 'provider': 'method', 'case_id': 'same',
     'state': 'pre_call_blocked_no_method_calls', 'native_route_receipt': {'transport_state': 'started'}},
])
def test_ambiguous_or_called_receipt_cannot_remove_failure(tmp_path, receipt):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] = []
    spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                        'status': 'not_called', 'reason': 'Not called',
                        'receipt': write(tmp_path / 'receipt.json', receipt)}]
    with pytest.raises(ValueError, match='receipt does not identify'):
        build(tmp_path, spec)


def test_portable_audit_has_no_host_paths_and_optional_source_verification(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    entry = build(tmp_path, spec)
    audit = json.loads((entry.parent / 'saved-matrix-audit.json').read_text())
    assert all(set(item) == {'name', 'sha256'} for item in audit['sources'])
    assert str(tmp_path) not in (entry.parent / 'saved-matrix-audit.json').read_text()
    assert check_saved_matrix(entry.parent)['source_files_verified'] == 0
    assert check_saved_matrix(entry.parent, spec_path=tmp_path / 'spec.json')['source_files_verified'] > 0


def test_colliding_suite_case_routes_rejected_before_output(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    dataset = json.loads(Path(spec['suites'][0]['dataset']['path']).read_text())
    dataset['cases'][0]['id'] = 'nested--same'
    spec['suites'][0]['dataset'] = write(tmp_path/'collision.json', dataset)
    spec['suites'].append({'id': 'first--nested', 'label': 'Nested',
                           'dataset': spec['sources'][0].get('dataset') or
                           write(tmp_path/'nested.json', {'cases': [{**dataset['cases'][0], 'id':'same'}]})})
    with pytest.raises(ValueError, match='Colliding suite-qualified case routes'):
        build(tmp_path, spec)
    assert not (tmp_path/'report').exists()


@pytest.mark.parametrize('before,after,selected', [(False, True, True), (True, True, False),
                                                   (False, False, False), (True, False, False)])
def test_single_case_parameter_group_preserves_both_calls_and_selects_improvement(tmp_path, before, after, selected):
    spec, root, record, _ = fixture(tmp_path)
    record['metrics']['content_pass'] = before
    (root/'results.jsonl').write_text(json.dumps(record)+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()
    candidate = tmp_path/'candidate'
    import shutil
    shutil.copytree(root, candidate)
    changed = deepcopy(record)
    changed.update(attempt_id='new-run', started_at='2026-11-01T01:00:00Z')
    changed['metrics']['content_pass'] = after
    (candidate/'results.jsonl').write_text(json.dumps(changed)+'\n')
    contract = write(tmp_path/'contract.json', {'created_at':'2026-11-01T00:00:00Z',
        'max_failure_retries':0, 'jobs':[{'id':'job','suite':'first','provider':'method','case_ids':['same']}]})
    spec['parameter_reruns'] = {'contract':contract,'selection':'replace_track_if_pass_count_improves',
        'sources':[{'job_id':'job','root':str(candidate),
                    'dataset':write(candidate/'dataset.json', json.loads((root/'dataset.json').read_text())),
                    'results':{'path':str(candidate/'results.jsonl'),
                               'sha256':hashlib.sha256((candidate/'results.jsonl').read_bytes()).hexdigest()}}]}
    report = build(tmp_path, spec).parent
    model = json.loads((report/'matrix.json').read_text())
    row = model['rows'][0]
    assert row['parameter_rerun']['selected_new'] is selected
    assert 'data-parameter-reruns' in (report/'index.html').read_text()
    assert 'data-parameter-history' in (report/'changes/index.html').read_text()
    assert row['passed'] is (before or after)
    actual = json.loads((report/row['record_path']).read_text())
    assert actual['attempt_id'] == ('new-run' if selected else 'run-001')
    for label in ('before','candidate'):
        assert (report/row['parameter_rerun'][label+'_record_path']).is_file()
    assert check_saved_matrix(report, spec_path=tmp_path/'spec.json')['valid']
    # A truncated cohort or changed rule cannot enter the report.
    (candidate/'results.jsonl').write_text('')
    spec['parameter_reruns']['sources'][0]['results']['sha256'] = hashlib.sha256(b'').hexdigest()
    with pytest.raises(ValueError, match='Incomplete parameter'):
        build(tmp_path, spec, 'incomplete')
    (candidate/'results.jsonl').write_text(json.dumps(changed)+'\n')
    spec['parameter_reruns']['sources'][0]['results']['sha256'] = hashlib.sha256((candidate/'results.jsonl').read_bytes()).hexdigest()
    data = json.loads((candidate/'dataset.json').read_text())
    data['cases'][0]['expected_any'] = ['different rule']
    spec['parameter_reruns']['sources'][0]['dataset'] = write(candidate/'dataset.json', data)
    with pytest.raises(ValueError, match='changed task rules'):
        build(tmp_path, spec, 'changed-rule')


def test_parameter_rerun_parts_must_cover_the_whole_original_job(tmp_path):
    from harness.web_fetch.report_saved_matrix import _parameter_reruns
    spec, root, record, _ = fixture(tmp_path)
    case = json.loads((root/'dataset.json').read_text())['cases'][0]
    second = {**case, 'id':'second'}
    base = tmp_path/'parts'
    sources = []
    selected = {}
    for cid in ('same', 'second'):
        part = base/cid
        (part/'artifacts').mkdir(parents=True)
        (part/'artifacts/answer.txt').write_text('answer')
        result = {**record, 'case_id':cid, 'attempt_id':cid}
        dataset = write(part/'dataset.json', {'cases':[case if cid=='same' else second]})
        raw = (json.dumps(result)+'\n').encode()
        (part/'results.jsonl').write_bytes(raw)
        sources.append({'job_id':'job','case_ids':[cid],'root':str(part),'dataset':dataset,
                        'results':{'path':str(part/'results.jsonl'),
                                   'sha256':hashlib.sha256(raw).hexdigest()}})
        selected[('first','method',cid)] = (part, result, raw, part/'results.jsonl')
    contract = write(tmp_path/'contract.json', {'created_at':'2026-10-28T00:00:00Z',
        'max_failure_retries':1,'jobs':[{'id':'job','suite':'first',
                                       'provider':'method','case_ids':['same','second']}]})
    from harness.web_fetch.report_saved_matrix import _SourceBindings
    inputs = {'spec':{'parameter_reruns':{'contract':contract,
              'selection':'replace_track_if_pass_count_improves','sources':sources}},
              'sources':_SourceBindings(tmp_path),'selected':selected,
              'revision':None,'cases':{'first':{'same':case,'second':second}}}
    assert len(_parameter_reruns(inputs)) == 2
    inputs['spec']['parameter_reruns']['sources'] = sources[:1]
    with pytest.raises(ValueError, match='Incomplete parameter rerun job inventory'):
        _parameter_reruns(inputs)
    inputs['spec']['parameter_reruns']['sources'] = [sources[0], sources[0], sources[1]]
    with pytest.raises(ValueError, match='declared population'):
        _parameter_reruns(inputs)


@pytest.mark.parametrize('after,selected', [([False, True, True], True),
                                           ([False, True, False], False),
                                           ([False, False, False], False)])
def test_whole_parameter_group_never_keeps_a_regression_from_the_old_group(after, selected):
    from harness.web_fetch.report_saved_matrix import _parameter_group_decisions
    keys = [('main', 'method', 'a'), ('main', 'method', 'b'), ('supplement', 'method', 'c')]
    before = [True, False, False]
    def record(passed):
        return {'ok':True, 'metrics':{'content_pass':passed, 'request_ok':True}}
    cases = {'main':{'a':{'track':'dynamic'}, 'b':{'track':'dynamic'}},
             'supplement':{'c':{'track':'dynamic'}}}
    inputs = {'revision':None, 'cases':cases,
              'selected':{k:(None,record(p),None,None) for k,p in zip(keys,before)}}
    candidates = {k:(None,record(p),None,None) for k,p in zip(keys,after)}
    decisions = _parameter_group_decisions(inputs, candidates)
    assert all(d['selected_new'] is selected for d in decisions.values())
    # When the group improves, its formerly passing first case must also use the new failure.
    chosen = [after[i] if decisions[k]['selected_new'] else before[i] for i,k in enumerate(keys)]
    assert chosen == (after if selected else before)
    assert decisions[keys[0]]['count'] == 3
    with pytest.raises(ValueError, match='complete method/track group'):
        _parameter_group_decisions(inputs, {keys[0]:candidates[keys[0]]})


@pytest.mark.parametrize('final_pass', [False, True])
def test_parameter_candidate_view_retains_a_separately_completed_retry(tmp_path, final_pass):
    spec, root, record, _ = fixture(tmp_path/'baseline')
    record['metrics']['content_pass'] = False
    (root/'results.jsonl').write_text(json.dumps(record)+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()
    candidate_spec, candidate_root, candidate, _ = fixture(tmp_path/'candidate')
    candidate['started_at'] = '2026-11-01T00:00:00Z'
    candidate['metrics']['content_pass'] = final_pass
    raw = (json.dumps(candidate)+'\n').encode()
    (candidate_root/'results.jsonl').write_bytes(raw)
    candidate_spec['sources'][0]['results']['sha256'] = hashlib.sha256(raw).hexdigest()
    first = deepcopy(candidate)
    first['started_at'] = '2026-10-31T01:00:00Z'
    first['metrics']['content_pass'] = False
    first_raw = (json.dumps(first)+'\n').encode()
    (candidate_root/'prior.jsonl').write_bytes(first_raw)
    launch = write(tmp_path/'retry-launch.json', {'started_at':'2026-10-31T02:00:00Z',
        'total_authorized_physical_calls':2,'first_record':{'record':first}})
    candidate_spec['retry_continuations'] = [{'suite':'first','provider':'method','case_id':'same',
        'current_record_sha256':hashlib.sha256(raw).hexdigest(),'launch_contract':launch,
        'prior_source':{'root':str(candidate_root),'dataset':candidate_spec['suites'][0]['dataset'],
                        'results':{'path':str(candidate_root/'prior.jsonl'),
                                   'sha256':hashlib.sha256(first_raw).hexdigest()}}}]
    candidate_binding = write(tmp_path/'candidate-spec.json', candidate_spec)
    contract = write(tmp_path/'parameter-contract.json', {'created_at':'2026-10-30T00:00:00Z',
        'max_failure_retries':1,'jobs':[{'id':'job','suite':'first','provider':'method','case_ids':['same']}]})
    spec['parameter_reruns'] = {'contract':contract,'selection':'replace_track_if_pass_count_improves',
        'sources':[{'job_id':'job','saved_matrix_spec':candidate_binding}]}
    output = build(tmp_path,spec).parent
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    proof = row['parameter_rerun']
    assert (output/proof['candidate_prior_record_path']).read_bytes() == first_raw
    assert proof['selected_new'] is final_pass
    if final_pass:
        assert row['retries'] == 1 and row['retry_continuation']['total_physical_calls'] == 2
    assert check_saved_matrix(output,spec_path=tmp_path/'spec.json')['valid']
    for change, message in [({'max_failure_retries': 0}, 'retry allowance'),
                            ({'created_at': '2026-10-31T01:30:00Z'}, 'predates')]:
        contract_path = tmp_path / 'parameter-contract.json'
        data = json.loads(contract_path.read_text())
        changed_contract = {**data, **change}
        spec['parameter_reruns']['contract'] = write(tmp_path / 'invalid-contract.json', changed_contract)
        with pytest.raises(ValueError, match=message):
            build(tmp_path, spec, 'invalid-' + message)


@pytest.mark.parametrize('extra', ['private_artifacts/raw.json', 'evidence/extra.html', 'extra-link'])
def test_portable_checker_rejects_unbound_files_and_symlinks(tmp_path, extra):
    spec, _, _, _ = fixture(tmp_path)
    report = build(tmp_path, spec).parent
    assert check_saved_matrix(report)['valid']
    path = report / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    if extra == 'extra-link':
        path.symlink_to(report / 'matrix.json')
    else:
        path.write_text('unbound evidence')
    with pytest.raises(ValueError, match='inventory changed|symlink'):
        check_saved_matrix(report)


@pytest.mark.parametrize('duplicate', ['job', 'case'])
def test_parameter_contract_rejects_duplicate_scope_before_sources(tmp_path, duplicate):
    from harness.web_fetch.report_saved_matrix import _parameter_reruns, _SourceBindings
    job = {'id': 'job', 'case_ids': ['same']}
    jobs = [job, dict(job)] if duplicate == 'job' else [dict(job, case_ids=['same', 'same'])]
    contract = write(tmp_path / 'contract.json', {'jobs': jobs})
    inputs = {'spec': {'parameter_reruns': {
        'contract': contract, 'selection': 'replace_track_if_pass_count_improves', 'sources': []}},
        'sources': _SourceBindings(tmp_path)}
    with pytest.raises(ValueError, match='Duplicate parameter rerun'):
        _parameter_reruns(inputs)


@pytest.mark.parametrize('old_hash,new_hash,accepted', [
    ('same', 'same', True), ('old', 'new', False), (None, 'new', False)])
def test_parameter_scoring_requires_explicit_common_provenance(tmp_path, old_hash, new_hash, accepted):
    from harness.web_fetch.report_saved_matrix import _parameter_scoring
    _, _, record, _ = fixture(tmp_path)
    before = deepcopy(record)
    before['metrics']['scoring_source_sha256'] = old_hash
    record['metrics']['scoring_source_sha256'] = new_hash
    if accepted:
        _parameter_scoring(before, record)
    else:
        with pytest.raises(ValueError, match='explicit scoring revision'):
            _parameter_scoring(before, record)
