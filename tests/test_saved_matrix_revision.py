"""Offline score revisions retain the exact saved call population and predecessors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.local_revision import revise_local_snapshot
from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix
from lexbench_webfetch.scoring import score_attempt
from tests.test_saved_matrix_evidence import fixture, write, assert_proof_mutations_rejected, assert_revision_document_mutation_rejected, assert_saved_receipt_mutation_rejected, assert_scoring_flags_rejected, assert_whitespace_record_rejected


def revision_fixture(tmp_path, *, multiple=False, second_provider=False, uncalled=False, completion_note=False, answer=b'answer'):
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
    (source/row['artifact_path']).write_bytes(answer)
    row['metrics'] = score_attempt(case, answer, ok=True)
    (source/'results.jsonl').write_text(json.dumps(row)+'\n')
    if second_provider:
        spec['providers'].append({'id': 'other', 'label': 'Other'})
        with (source/'results.jsonl').open('a') as handle:
            handle.write(json.dumps({**row, 'provider': 'other', 'attempt_id': 'other-call'})+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
    if multiple:
        spec['suites'].append({**spec['suites'][0], 'id':'second', 'label':'Second'})
        spec['sources'].append({**spec['sources'][0], 'suite':'second'})
    write(tmp_path/'spec.json', spec)
    if completion_note:
        from unittest.mock import patch
        from harness.web_fetch.report import generate_saved_matrix
        def render_with_note(output, model):
            model['rows'][0]['completion_note'] = 'Only this case uses the declared DNS exception.'
            write(output/'matrix.json', model)
            return generate_saved_matrix(output, model)
        with patch('harness.web_fetch.report.generate_saved_matrix', side_effect=render_with_note):
            revision = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revision').parent
    else:
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


def test_revision_round_trip_preserves_call_with_completion_note(tmp_path):
    spec, source, _ = revision_fixture(tmp_path, completion_note=True)
    original = (source/'results.jsonl').read_bytes()
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised').parent
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['scores_changed']
    assert (source/'results.jsonl').read_bytes() == original


@pytest.mark.parametrize('missing_attachment', [False, True])
@pytest.mark.parametrize('root_revision', [False, True])
def test_adoption_only_revision_retains_attachments_and_score_provenance(tmp_path, missing_attachment, root_revision, monkeypatch):
    monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: {'request': 'fixture'})
    nested, _, revision = revision_fixture(tmp_path/'nested', answer=b'new answer')
    nested['offline_revision']['review_files'] = {
        'decisions': write(tmp_path/'nested/decisions.json', [{'reason': 'independent scope review'}])}
    spec, source, record, _ = fixture(tmp_path/'parent')
    case = json.loads((revision/'dataset.json').read_text())['cases'][0]
    case['id'] = 'same'
    case.pop('report_family', None)
    case.pop('report_family_label', None)
    spec['suites'][0]['dataset'] = write(source/'dataset.json', {'cases': [case]})
    record['metrics'] = score_attempt(case, b'answer', ok=True)
    spec['sources'][0]['results'] = write(source/'results.jsonl', record)
    spec['report_families'] = [{'id': 'web', 'label': 'Static', 'source_suites': ['first']}]
    if root_revision:
        write(tmp_path/'root-spec.json', spec)
        parent = build_saved_matrix(tmp_path/'root-spec.json', tmp_path/'root-revision').parent
        config = parent/'config.json'
        write(config, {'cases': [], 'rescore_attempt_ids': ['first--run-001'],
                       'authority': {'description': 'Align scoring code without changing rules'},
                       'regression_ids': ['article-primary-scope-complete-without-shell',
                                          'article-primary-scope-shell-cannot-replace-body']})
        revise_local_snapshot(parent, config)
        receipt = next((parent/'local-evidence/offline-revisions').glob('*/receipt.json'))
        spec['offline_revision'] = {'root': str(parent), 'receipt': {
            'path': str(receipt), 'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()}}
    spec['adoptions'] = [{
        'authority': write(tmp_path/'authority.json', {
            'kind': 'user_selected_complete_report_groups', 'description': 'Select complete group.',
            'groups': [{'provider': 'method', 'family': 'web'}]}),
        'saved_matrix_spec': write(tmp_path/'nested/spec.json', nested),
        'execution_contract': write(tmp_path/'execution-contract.json', {
            'created_at': '2026-10-01T00:00:00Z', 'provider_config_sha256': 'a'*64,
            'timeout_seconds': 30, 'max_failure_retries': 0})}]
    write(tmp_path/'spec.json', spec)
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'report').parent
    matrix = json.loads((output/'matrix.json').read_text())
    assert_whitespace_record_rejected(output, matrix['rows'][0]['record_path'], tmp_path/'spec.json')
    assert_whitespace_record_rejected(output, matrix['rows'][0]['score_revision']['before_record_path'], tmp_path/'spec.json')
    if root_revision:
        assert_whitespace_record_rejected(output, matrix['rows'][0]['score_revision_history']['before_record_path'], tmp_path/'spec.json')
        original_path = output/matrix['rows'][0]['score_revision_history']['before_record_path']
        original_record = json.loads(original_path.read_text())
        assert (original_path.parent/original_record['artifact_path']).read_bytes() == b'answer'
        current_path = output/matrix['rows'][0]['record_path']
        current_record = json.loads(current_path.read_text())
        assert (current_path.parent/current_record['artifact_path']).read_bytes() == b'new answer'
    assert matrix['scores_changed'] is True
    assert json.loads((output/'execution.json').read_text())['scores_changed'] is True
    receipt_path = output/matrix['rows'][0]['score_revision']['receipt_path']
    receipt = json.loads(receipt_path.read_text())
    attachment = receipt['review_files']['decisions']
    assert attachment.startswith('adoption-review-assets/0/')
    assert (output/attachment).read_bytes() == (tmp_path/'nested/decisions.json').read_bytes()
    from bs4 import BeautifulSoup
    review = BeautifulSoup((output/'review/index.html').read_text(), 'html.parser')
    assert '调整 1 个案例，统一重算 1 份保存结果' in review.select_one('#current-revision-scope').get_text()
    if root_revision:
        assert '不是当前报告的净变化' in review.get_text()
        assert '历史评分变化分步记录' in review.get_text()
    adopted = review.select_one('#adopted-score-revisions')
    assert adopted is not None and 'method' in adopted.get_text()
    assert any(link.get('href') == '../'+attachment for link in adopted.select('a'))
    assert review.select_one('#score-revision-evidence') is not None
    changes = BeautifulSoup((output/'changes/index.html').read_text(), 'html.parser')
    assert changes.select_one('[data-adopted-revision-history]') is not None
    assert '本报告没有登记结果修订' not in changes.get_text()
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['scores_changed'] is True
    if root_revision:
        artifact = original_path.parent/original_record['artifact_path']
        audit_path = output/'saved-matrix-audit.json'
        original_audit = audit_path.read_bytes()
        artifact.write_bytes(b'new answer')
        audit = json.loads(original_audit)
        audit['files'][artifact.relative_to(output).as_posix()] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        write(audit_path, audit)
        for source_spec in (None, tmp_path/'spec.json'):
            with pytest.raises(ValueError, match='Portable saved record artifact differs'):
                check_saved_matrix(output, spec_path=source_spec)
        artifact.write_bytes(b'answer')
        audit_path.write_bytes(original_audit)
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'result_adoption', ['redirect_authority', 'remove'])
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'history', ['redirect_scope'])
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'score_revision', ['remove'])
    if root_revision:
        assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'score_revision_history', ['remove'])
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'completion_note',
                                    ['change_note', 'remove'], error='completion note differs')
    for relative in (receipt_path.relative_to(output).as_posix(), attachment,
                     matrix['rows'][0]['score_revision']['before_case_path']):
        assert_revision_document_mutation_rejected(output, tmp_path/'spec.json', relative)
    if root_revision:
        for relative in ('score-revision.json', matrix['rows'][0]['score_revision_history']['before_case_path']):
            assert_revision_document_mutation_rejected(output, tmp_path/'spec.json', relative)
    assert_saved_receipt_mutation_rejected(output, tmp_path/'spec.json')
    assert_scoring_flags_rejected(output, tmp_path/'spec.json')
    if missing_attachment:
        receipt['review_files']['decisions'] = 'missing.json'
        binding = write(receipt_path, receipt)
        audit = json.loads((output/'saved-matrix-audit.json').read_text())
        audit['files'][receipt_path.relative_to(output).as_posix()] = binding['sha256']
        write(output/'saved-matrix-audit.json', audit)
        with pytest.raises(ValueError, match='Missing score revision attachment'):
            check_saved_matrix(output)


def test_saved_revision_preserves_calls_and_exposes_original_scores(tmp_path):
    spec, source, revision = revision_fixture(tmp_path)
    raw = (source/'results.jsonl').read_bytes()
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised').parent
    matrix = json.loads((output/'matrix.json').read_text())
    assert matrix['totals']['passed'] == 1
    row = matrix['rows'][0]
    assert (output/row['score_revision']['before_record_path']).read_bytes() == raw
    assert (source/'results.jsonl').read_bytes() == raw
    assert_saved_receipt_mutation_rejected(output, tmp_path/'spec.json')
    assert_scoring_flags_rejected(output, tmp_path/'spec.json')
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'score_revision', ['remove'])
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'score_revision_history', ['remove'])
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


@pytest.mark.parametrize('with_parameter_candidates', [False, True])
def test_exclusion_scopes_revision_counts_and_decisions_but_preserves_history(tmp_path, monkeypatch, with_parameter_candidates):
    spec, _, _ = revision_fixture(tmp_path, multiple=True)
    if with_parameter_candidates:
        # Reuse bound calls to exercise assembly of parameter proof alongside
        # scoped review attachments; transport validation has separate controls.
        spec['parameter_reruns'] = {'selection': 'replace_track_if_pass_count_improves'}
        monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_reruns',
                            lambda inputs: inputs['selected'])
    decisions = [{'suite': suite, 'case_id': 'same', 'rule_decisions': [],
                  'decision_basis': suite+' independent decision'} for suite in ('first', 'second')]
    spec['offline_revision']['review_files'] = {'decisions': write(tmp_path/'decisions.json', decisions)}
    spec['report_families'] = [
        {'id': 'web', 'label': 'Retained', 'source_suites': ['second']},
        {'id': 'base-web', 'task_family': 'web', 'label': 'Excluded', 'source_suites': ['first']}]
    spec['excluded_report_families'] = ['base-web']
    write(tmp_path/'spec.json', spec)
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'report').parent
    if with_parameter_candidates:
        row = json.loads((output/'matrix.json').read_text())['rows'][0]
        assert row['parameter_rerun']['selected_new'] is False
    receipt = json.loads((output/'score-revision.json').read_text())
    history = json.loads((output/receipt['historical_scope_path']).read_text())
    assert receipt['changed_cases'] == [] and receipt['rescored_results'] == 0
    assert history['changed_cases'] == ['first--same'] and history['rescored_results'] == 1
    assert all(not step['task_verdict_changes'] for step in receipt['steps'])
    assert json.loads((output/receipt['review_files']['decisions']).read_text()) == decisions[1:]
    assert json.loads((output/history['review_files']['decisions']).read_text()) == decisions
    review = (output/'review/index.html').read_text()
    assert 'first independent decision' not in review and 'second independent decision' in review
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']
    for relative in ('score-revision.json', 'score-revision-history.json', 'review-assets/decisions.json', 'report-review-assets/decisions.json'):
        assert_revision_document_mutation_rejected(output, tmp_path/'spec.json', relative)


def test_revision_rejects_different_frozen_call_even_with_rebound_source(tmp_path):
    spec, source, _ = revision_fixture(tmp_path)
    row = json.loads((source/'results.jsonl').read_text())
    row['elapsed_ms'] = 99
    (source/'results.jsonl').write_text(json.dumps(row)+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
    write(tmp_path/'spec.json', spec)
    with pytest.raises(ValueError, match='predecessor result differs'):
        build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised')


@pytest.mark.parametrize('tie', [False, True])
@pytest.mark.parametrize('authorized', [False, True])
def test_completion_candidate_baseline_requires_explicit_whole_group_reselection(tmp_path, monkeypatch, tie, authorized):
    monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: {'request': 'fixture'})
    spec, source, before, _ = fixture(tmp_path)
    before['metrics']['content_pass'] = not tie
    spec['sources'][0]['results'] = write(source/'results.jsonl', before)
    candidate = deepcopy(before)
    candidate['attempt_id'] = 'candidate-call'
    candidate['metrics']['content_pass'] = False
    candidate_binding = write(source/'candidate.jsonl', candidate)
    baseline_spec = deepcopy(spec)
    baseline_spec['sources'][0]['results'] = candidate_binding
    write(tmp_path/'baseline-spec.json', baseline_spec)
    baseline = build_saved_matrix(tmp_path/'baseline-spec.json', tmp_path/'baseline').parent
    spec['completion'] = {
        'baseline_matrix': {'path': str(baseline/'matrix.json'), 'sha256': hashlib.sha256((baseline/'matrix.json').read_bytes()).hexdigest()},
        'baseline_audit': {'path': str(baseline/'saved-matrix-audit.json'), 'sha256': hashlib.sha256((baseline/'saved-matrix-audit.json').read_bytes()).hexdigest()},
        'contract': write(tmp_path/'completion.json', {'created_at': '2026-10-28T00:00:00Z', 'provider': 'method', 'jobs': [], 'cases': 0})}
    if authorized:
        spec['parameter_reruns'] = {
            'selection': 'replace_track_if_pass_count_improves',
            'contract': write(tmp_path/'parameters.json', {'created_at': '2026-10-28T00:00:00Z',
                'max_failure_retries': 0, 'jobs': [{'id': 'job', 'suite': 'first', 'provider': 'method', 'case_ids': ['same']}]}),
            'sources': [{'job_id': 'job', 'root': str(source), 'dataset': spec['suites'][0]['dataset'], 'results': candidate_binding}]}
    write(tmp_path/'spec.json', spec)
    if not authorized:
        with pytest.raises(ValueError, match='retained baseline call'):
            build_saved_matrix(tmp_path/'spec.json', tmp_path/'report')
        return
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'report').parent
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    assert row['parameter_rerun']['selected_new'] is False
    assert json.loads((output/row['record_path']).read_text())['attempt_id'] == before['attempt_id']
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']


@pytest.mark.parametrize('defect', [None, 'already_called', 'before_contract',
                                  'early_first_call', 'duplicate', 'incomplete', 'undeclared', 'extra_baseline', 'retained_call', 'retained_task', 'completed_task', 'extra_uncommitted', 'missing_execution', 'missing_retry_allowance'])
@pytest.mark.parametrize('contract_form', ['single', 'multiple'])
def test_completion_preserves_frozen_offline_history(tmp_path, defect, contract_form, monkeypatch):
    # History controls isolate execution proof; real native authorization has its
    # own positive/negative transport fixtures. The absent-proof case uses production.
    if defect != 'missing_execution':
        monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: {'request': 'fixture'})
    spec, source, revision = revision_fixture(tmp_path, uncalled=True)
    baseline = {'path': str(revision/'matrix.json'), 'sha256': hashlib.sha256((revision/'matrix.json').read_bytes()).hexdigest()}
    record = json.loads((source/'results.jsonl').read_text())
    record.update(case_id='later', attempt_id='later-run', started_at='2026-11-02T00:00:00Z')
    record['metrics'] = score_attempt({'id': 'later', 'url': record['url']}, b'answer', ok=True)
    if defect == 'before_contract':
        record['started_at'] = '2026-10-01T00:00:00Z'
    if defect == 'early_first_call':
        record['provider_metadata']['retry'] = {'physical_attempts': 1, 'retries_used': 0,
            'attempts': [{'started_at': '2026-10-01T00:00:00Z'}]}
    results = write(source/'completion.jsonl', record)
    spec['sources'].append({**deepcopy(spec['sources'][0]), 'case_ids': ['later'], 'results': results})
    spec['missing'] = []
    contract = {'created_at': '2026-11-01T00:00:00Z', 'provider': 'method', 'cases': 1, 'max_failure_retries': 0,
                'jobs': [{'suite': 'first', 'case_ids': ['later']}]}
    if defect == 'missing_retry_allowance':
        del contract['max_failure_retries']
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
        message = {'missing_retry_allowance': 'explicit nonnegative retry allowance',
                   'missing_execution': 'lacks its execution contract',
                   'extra_uncommitted': 'undeclared position',
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


def test_provider_exclusion_scopes_revision_without_changing_originals(tmp_path):
    spec, source, _ = revision_fixture(tmp_path, second_provider=True)
    original = (source/'results.jsonl').read_bytes()
    spec['excluded_report_providers'] = ['other']
    write(tmp_path/'spec.json', spec)
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised').parent
    receipt = json.loads((output/'score-revision.json').read_text())
    history = json.loads((output/receipt['historical_scope_path']).read_text())
    assert receipt['rescored_results'] == 1 and history['rescored_results'] == 2
    assert len(receipt['steps'][0]['task_verdict_changes']) == 1
    assert len(history['steps'][0]['task_verdict_changes']) == 2
    assert (source/'results.jsonl').read_bytes() == original
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']
    for relative in ('score-revision.json', 'score-revision-history.json'):
        assert_revision_document_mutation_rejected(output, tmp_path/'spec.json', relative)
