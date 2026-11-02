"""Offline score revisions retain the exact saved call population and predecessors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.local_revision import revise_local_snapshot
from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix
from lexbench_webfetch.scoring import score_attempt
from tests.test_saved_matrix_evidence import fixture, write


def revision_fixture(tmp_path, *, multiple=False, uncalled=False):
    spec, source, row, _ = fixture(tmp_path)
    case = json.loads((source/'dataset.json').read_text())['cases'][0]
    case['required_facts'] = [{'id':'shell', 'alternatives':['Subscribe']}]
    spec['suites'][0]['dataset'] = write(source/'dataset.json', {'cases':[case]})
    if uncalled:
        extra = {**deepcopy(case), 'id': 'later', 'required_facts': []}
        spec['suites'][0]['dataset'] = write(source/'dataset.json', {'cases': [case, extra]})
        receipt = write(tmp_path/'missing.json', {'blocked_cases': [
            {'suite': 'first', 'provider': 'method', 'case_id': 'later',
             'state': 'pre_call_blocked_no_method_calls',
             'native_route_receipt': {'transport_state': 'not_started'}}]})
        spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'later',
                            'status': 'not_called', 'reason': 'DNS unavailable', 'receipt': receipt}]
    row['metrics'] = score_attempt(case, b'answer', ok=True)
    (source/'results.jsonl').write_text(json.dumps(row)+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
    if multiple:
        spec['suites'].append({**spec['suites'][0], 'id':'second', 'label':'Second'})
        spec['sources'].append({**spec['sources'][0], 'suite':'second'})
    write(tmp_path/'spec.json', spec)
    revision = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revision').parent
    dataset = json.loads((revision/'dataset.json').read_text())
    changed = deepcopy(dataset['cases'][0])
    changed['required_facts'] = []
    config = revision/'config.json'
    write(config, {'cases':[changed], 'rule_revision':True,
        'authority':{'description':'Remove independently identified subscription control'},
        'regression_ids':['article-primary-scope-complete-without-shell',
                          'article-primary-scope-shell-cannot-replace-body']})
    revise_local_snapshot(revision, config)
    receipt = next((revision/'local-evidence/offline-revisions').glob('*/receipt.json'))
    spec['offline_revision'] = {'root':str(revision),
        'receipt':{'path':str(receipt),'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}}
    write(tmp_path/'spec.json', spec)
    return spec, source, revision


def test_saved_revision_preserves_calls_and_exposes_original_scores(tmp_path):
    spec, source, revision = revision_fixture(tmp_path)
    raw = (source/'results.jsonl').read_bytes()
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised').parent
    matrix = json.loads((output/'matrix.json').read_text())
    assert matrix['totals']['passed'] == 1
    row = matrix['rows'][0]
    assert (output/row['score_revision']['before_record_path']).read_bytes() == raw
    assert (source/'results.jsonl').read_bytes() == raw
    assert json.loads((output/row['record_path']).read_text())['elapsed_ms'] == 12
    assert json.loads((output/'score-revision.json').read_text())['provider_calls_added'] == 0
    from bs4 import BeautifulSoup
    review = BeautifulSoup((output/'review/index.html').read_text(), 'html.parser')
    steps = review.select_one('#score-revision-steps')
    assert steps is not None
    assert [cell.get_text(strip=True) for cell in steps.select('tbody tr td')] == ['1', '规则修订', '1', '0']
    changes = BeautifulSoup((output/'changes/index.html').read_text(), 'html.parser')
    assert '1 个案例调整规则，1 份保存结果统一重评' in changes.get_text()
    assert changes.select_one('.review-round a')['href'] == '../review/index.html'
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['scores_changed'] is True


@pytest.mark.parametrize('defect', ['answer', 'receipt', 'dataset'])
def test_saved_revision_rejects_tampered_evidence(tmp_path, defect):
    spec, source, revision = revision_fixture(tmp_path)
    if defect == 'answer':
        (revision/'evidence/first/method/same/artifacts/answer.txt').write_text('other answer')
    elif defect == 'receipt':
        Path(spec['offline_revision']['receipt']['path']).write_text('{}')
    else:
        dataset = json.loads((revision/'dataset.json').read_text())
        dataset['cases'][0]['url'] = 'https://other.test/'
        write(revision/'dataset.json', dataset)
    with pytest.raises(ValueError):
        build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised')


def test_revision_keeps_same_id_in_other_suite_unchanged(tmp_path):
    _, source, _ = revision_fixture(tmp_path, multiple=True)
    raw = (source/'results.jsonl').read_bytes()
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised').parent
    assert (output/'evidence/second/method/same/record.json').read_bytes() == raw
    rows = json.loads((output/'matrix.json').read_text())['rows']
    assert [row['passed'] for row in rows] == [True, False]
    review = (output/'review/index.html').read_text()
    assert 'score-before-record.json' in review and '修订前后证据' in review


def test_revision_rejects_different_frozen_call_even_with_rebound_source(tmp_path):
    spec, source, _ = revision_fixture(tmp_path)
    row = json.loads((source/'results.jsonl').read_text())
    row['elapsed_ms'] = 99
    (source/'results.jsonl').write_text(json.dumps(row)+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
    write(tmp_path/'spec.json', spec)
    with pytest.raises(ValueError, match='predecessor result differs'):
        build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised')


@pytest.mark.parametrize('defect', [None, 'already_called', 'before_contract',
                                  'early_first_call', 'duplicate', 'incomplete', 'undeclared', 'extra_baseline', 'retained_call', 'retained_task', 'completed_task', 'extra_uncommitted'])
@pytest.mark.parametrize('contract_form', ['single', 'multiple'])
def test_completion_preserves_frozen_offline_history(tmp_path, defect, contract_form):
    spec, source, revision = revision_fixture(tmp_path, uncalled=True)
    baseline = {'path': str(revision/'matrix.json'), 'sha256': hashlib.sha256((revision/'matrix.json').read_bytes()).hexdigest()}
    record = json.loads((source/'results.jsonl').read_text())
    record.update(case_id='later', attempt_id='later-run', started_at='2026-11-02T00:00:00Z')
    record['metrics'] = score_attempt({'id': 'later', 'url': record['url']}, b'answer', ok=True)
    if defect == 'before_contract':
        record['started_at'] = '2026-10-01T00:00:00Z'
    if defect == 'early_first_call':
        record['provider_metadata']['retry'] = {'attempts': [{'started_at': '2026-10-01T00:00:00Z'}]}
    results = write(source/'completion.jsonl', record)
    spec['sources'].append({**deepcopy(spec['sources'][0]), 'case_ids': ['later'], 'results': results})
    spec['missing'] = []
    contract = {'created_at': '2026-11-01T00:00:00Z', 'provider': 'method', 'cases': 1,
                'jobs': [{'suite': 'first', 'case_ids': ['later']}]}
    if defect == 'already_called':
        old = json.loads(Path(baseline['path']).read_text())
        old['rows'][1].update(status='saved', called=True, record_path='record.json')
        baseline = write(tmp_path/'baseline-matrix.json', old)
    if defect == 'extra_baseline':
        old = json.loads(Path(baseline['path']).read_text())
        old['rows'].append({**old['rows'][0], 'case_id': 'unrelated'})
        baseline = write(tmp_path/'baseline-matrix.json', old)
    if defect == 'incomplete':
        contract['jobs'][0]['case_ids'] = ['absent']
    if defect == 'duplicate':
        contract['jobs'][0]['case_ids'] = ['later', 'later']
        contract['cases'] = 2
    if defect == 'extra_uncommitted':
        old = json.loads(Path(baseline['path']).read_text())
        old['rows'][0].update(called=False, status='not_called', record_path=None)
        baseline = write(tmp_path/'baseline-matrix.json', old)
        spec['sources'] = spec['sources'][1:]
        spec.pop('offline_revision')
        spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                            'status': 'uncommitted', 'reason': 'Collection failed',
                            'receipt': write(tmp_path/'uncommitted.json', {
                                'suite': 'first', 'provider': 'method', 'case_id': 'same',
                                'state': 'called_but_uncommitted', 'elapsed_ms': None})}]
    if defect in {'retained_call', 'retained_task', 'completed_task'}:
        old = json.loads(Path(baseline['path']).read_text())
        relative = old['rows'][1 if defect == 'completed_task' else 0][
            'record_path' if defect == 'retained_call' else 'case_path']
        path = revision/relative
        altered = json.loads(path.read_text())
        altered['elapsed_ms' if defect == 'retained_call' else 'url'] = 999 if defect == 'retained_call' else 'https://different.example/'
        replacement = write(path, altered)
        audit = json.loads((revision/'saved-matrix-audit.json').read_text())
        audit['files'][relative] = replacement['sha256']
        write(revision/'saved-matrix-audit.json', audit)
    spec['completion'] = {'baseline_matrix': baseline,
                          'baseline_audit': {'path': str(revision/'saved-matrix-audit.json'),
                                             'sha256': hashlib.sha256((revision/'saved-matrix-audit.json').read_bytes()).hexdigest()},
                          'contract': write(tmp_path/'completion-contract.json', contract)}
    if contract_form == 'multiple':
        entry = spec.pop('completion')
        entry['note'] = 'Only this case uses Cloudflare DoH.'
        entry['suite'] = 'first'
        contract.pop('provider')
        contract['providers'] = ['method']
        contract['case_ids'] = contract.pop('jobs')[0]['case_ids']
        entry['contract'] = write(tmp_path/'completion-contract.json', contract)
        spec['completions'] = [entry]
    if defect == 'undeclared':
        spec.pop('completion', None)
        spec.pop('completions', None)
    write(tmp_path/'spec.json', spec)
    if defect == 'early_first_call':
        from harness.web_fetch.report_saved_matrix import _completed_missing, _SourceBindings
        with pytest.raises(ValueError, match='predates'):
            _completed_missing(spec, _SourceBindings(tmp_path),
                               {('first', 'method', 'later'): (source, record, b'', source/'completion.jsonl')})
        return
    if defect:
        message = {'extra_uncommitted': 'undeclared position',
                   'completed_task': 'undeclared task'}.get(defect)
        with pytest.raises(ValueError, match=message):
            build_saved_matrix(tmp_path/'spec.json', tmp_path/'completed')
        return
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'completed').parent
    model = json.loads((output/'matrix.json').read_text())
    assert model['totals']['saved'] == 2
    assert all(row['called'] for row in model['rows'])
    assert model['rows'][0]['score_revision']
    assert 'score_revision' not in model['rows'][1]
    if contract_form == 'multiple':
        assert model['rows'][1]['completion_note'] == 'Only this case uses Cloudflare DoH.'
        assert any('Only this case uses Cloudflare DoH.' in p.read_text() for p in (output/'attempts').glob('*.html'))
    assert json.loads((output/'score-revision.json').read_text())['provider_calls_added'] == 0
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']
