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


def assert_provenance_mutation_rejected(output, relative, spec):
    original = (output/relative).read_bytes()
    audit_path = output/'saved-matrix-audit.json'
    original_audit = audit_path.read_bytes()
    record = json.loads(original)
    record['error_message'] = 'altered historical explanation'
    bound = write(output/relative, record)
    audit = json.loads(original_audit)
    audit['files'][relative] = bound['sha256']
    write(audit_path, audit)
    try:
        with pytest.raises(ValueError, match='provenance record differs'):
            check_saved_matrix(output, spec_path=spec)
    finally:
        (output/relative).write_bytes(original)
        audit_path.write_bytes(original_audit)
    assert_whitespace_record_rejected(output, relative, spec)


def assert_redirected_answer_rejected(output, fields, spec):
    matrix_path, audit_path = output/'matrix.json', output/'saved-matrix-audit.json'
    original, original_audit = matrix_path.read_bytes(), audit_path.read_bytes()
    model = json.loads(original)
    value = model['rows'][0]
    for field in fields[:-1]:
        value = value[field]
    # Redirect to an existing, bound file rather than a missing target.
    value[fields[-1]] = model['rows'][0]['case_path']
    audit = json.loads(original_audit)
    audit['files']['matrix.json'] = write(matrix_path, model)['sha256']
    write(audit_path, audit)
    try:
        for source in (None, spec):
            with pytest.raises(ValueError, match='evidence link differs|proof differs'):
                check_saved_matrix(output, spec_path=source)
    finally:
        matrix_path.write_bytes(original)
        audit_path.write_bytes(original_audit)


def test_source_checker_preserves_status_with_dual_event_receipt(tmp_path):
    from harness.web_fetch.report_saved_matrix import _totals
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] = []
    events = [{'suite': 'first', 'provider': 'method', 'case_id': 'same', 'state': state}
              for state in ('pre_call_blocked_no_method_calls', 'called_but_uncommitted')]
    events[0]['native_route_receipt'] = {'transport_state': 'not_started'}
    spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                        'status': 'uncommitted', 'reason': 'Interrupted',
                        'receipt': write(tmp_path/'receipt.json', events)}]
    output = build(tmp_path, spec).parent
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']
    model = json.loads((output/'matrix.json').read_text())
    model['rows'][0].update(status='not_called', called=False)
    model['totals'] = _totals(model['rows'])
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['totals'] = model['totals']
    audit['files']['matrix.json'] = write(output/'matrix.json', model)['sha256']
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='Saved missing state differs from matrix|missing receipt differs from validated source'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json')


def test_selected_answer_link_cannot_be_redirected(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    assert_redirected_answer_rejected(output, ['answer_path'], tmp_path/'spec.json')


def assert_proof_mutations_rejected(output, spec, proof_name, mutations, error='proof differs from validated source'):
    from harness.web_fetch.report import generate_saved_matrix
    original = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    for mutation in mutations:
        model = json.loads(original[Path('matrix.json')])
        row = model['rows'][0]
        if proof_name == 'report_families':
            if mutation == 'remove':
                model.pop('report_families')
            else:
                model['report_families'][0]['source_suites'], model['report_families'][1]['source_suites'] = (
                    model['report_families'][1]['source_suites'], model['report_families'][0]['source_suites'])
        elif mutation == 'change_note':
            row[proof_name] = 'Different DNS policy.'
        elif mutation == 'remove':
            row.pop(proof_name)
            if proof_name == 'retry_continuation':
                row['retries'] = json.loads((output/row['record_path']).read_text()).get('retries', 0)
        elif mutation == 'redirect_scope':
            row[proof_name][0]['scope_path'] = row['case_path']
        elif mutation == 'redirect_authority':
            row[proof_name]['authority_path'] = row['case_path']
        elif mutation == 'group':
            row[proof_name]['group']['candidate_passed'] += 1
        else:
            old = row[proof_name][mutation]
            row[proof_name][mutation] = not old if isinstance(old, bool) else (old or 0)+1
        write(output/'matrix.json', model)
        for name in ('dataset.json', 'summary.json', 'execution.json', 'results.jsonl', 'en/index.html'):
            (output/name).unlink()
        generate_saved_matrix(output, model)
        audit = json.loads(original[Path('saved-matrix-audit.json')])
        audit['files'] = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in output.rglob('*') if p.is_file() and p.name != 'saved-matrix-audit.json'}
        write(output/'saved-matrix-audit.json', audit)
        try:
            with pytest.raises(ValueError, match=error):
                check_saved_matrix(output, spec_path=spec)
        finally:
            for path, raw in original.items():
                (output/path).write_bytes(raw)


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
                          'content_pass': True, 'request_ok': True, 'scoring_source_sha256': 'a'*64},
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


@pytest.mark.parametrize('destination', ['receipt.json', 'record.json', 'case.json'])
def test_captured_artifact_cannot_overwrite_generated_evidence(tmp_path, destination):
    spec, source, record, _ = fixture(tmp_path)
    captured = source/destination
    captured.write_bytes(b'immutable captured bytes')
    record['provider_metadata'].update(raw_stdout_path=destination, raw_stdout_sha256=hashlib.sha256(captured.read_bytes()).hexdigest())
    spec['sources'][0]['results'] = write(source/'results.jsonl', record)
    with pytest.raises(ValueError, match='destination collision'):
        build(tmp_path, spec)
    assert captured.read_bytes() == b'immutable captured bytes'


def test_portable_checker_revalidates_record_artifacts_after_audit_rebinding(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    (output/row['answer_path']).write_bytes(b'changed answer')
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files'][row['answer_path']] = hashlib.sha256(b'changed answer').hexdigest()
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='Portable saved record artifact differs'):
        check_saved_matrix(output)


@pytest.mark.parametrize('field', ['score', 'time', 'provider', 'called'])
@pytest.mark.parametrize('source_check', [False, True])
def test_portable_checker_reconciles_selected_record_with_row(tmp_path, field, source_check):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    record = json.loads((output/row['record_path']).read_text())
    if field == 'score':
        record['metrics']['content_pass'] = False
    elif field == 'time':
        record['elapsed_ms'] += 1
    elif field == 'provider':
        record['provider'] = 'other'
    else:
        record['provider_metadata']['native_route_receipt']['transport_state'] = 'not_started'
    bound = write(output/row['record_path'], record)
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files'][row['record_path']] = bound['sha256']
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='Saved record .* differ'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json' if source_check else None)


def test_source_checker_rejects_record_mutation_outside_visible_scalars(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    record = json.loads((output/row['record_path']).read_text())
    record['started_at'] = '2026-10-30T00:00:00Z'
    bound = write(output/row['record_path'], record)
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files'][row['record_path']] = bound['sha256']
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='validated source selection'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json')


@pytest.mark.parametrize('rewrite_dataset', [False, True])
def test_checker_rejects_missing_rows_after_rebinding_totals(tmp_path, rewrite_dataset):
    from harness.web_fetch.report_saved_matrix import _totals
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    model = json.loads((output/'matrix.json').read_text())
    model['rows'] = []
    model['totals'] = _totals([])
    bound = write(output/'matrix.json', model)
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['totals'] = model['totals']
    audit['files']['matrix.json'] = bound['sha256']
    if rewrite_dataset:
        dataset = json.loads((output/'dataset.json').read_text())
        dataset['cases'] = []
        audit['files']['dataset.json'] = write(output/'dataset.json', dataset)['sha256']
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='population differs'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json' if rewrite_dataset else None)


@pytest.mark.parametrize('mutation', ['score', 'time', 'case_rules', 'missing_result'])
@pytest.mark.parametrize('source_check', [False, True])
def test_checker_reconciles_renderer_projection_after_audit_rebinding(tmp_path, mutation, source_check):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    if mutation == 'case_rules':
        relative = 'dataset.json'
        dataset = json.loads((output/relative).read_text())
        dataset['cases'][0]['expected_any'] = ['different acceptance rule']
        write(output/relative, dataset)
    else:
        relative = 'results.jsonl'
        records = [json.loads(line) for line in (output/relative).read_text().splitlines()]
        if mutation == 'score':
            records[0]['metrics']['content_pass'] = False
        elif mutation == 'time':
            records[0]['elapsed_ms'] += 1
        else:
            records = []
        (output/relative).write_text(''.join(json.dumps(row)+'\n' for row in records))
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files'][relative] = hashlib.sha256((output/relative).read_bytes()).hexdigest()
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='renderer projection differs'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json' if source_check else None)


@pytest.mark.parametrize('defect', [None, 'downgrade', 'receipt'])
def test_completion_preserves_uncommitted_baseline_state_and_receipt(tmp_path, defect):
    spec, source, _, _ = fixture(tmp_path)
    spec['sources'] = []
    receipt = {'suite': 'first', 'provider': 'method', 'case_id': 'same',
               'state': 'called_but_uncommitted', 'elapsed_ms': None}
    spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                        'status': 'uncommitted', 'reason': 'Collection interrupted',
                        'receipt': write(tmp_path/'receipt.json', receipt)}]
    baseline = build(tmp_path, spec, 'baseline').parent
    spec['completion'] = {
        'baseline_matrix': {'path': str(baseline/'matrix.json'),
                            'sha256': hashlib.sha256((baseline/'matrix.json').read_bytes()).hexdigest()},
        'baseline_audit': {'path': str(baseline/'saved-matrix-audit.json'),
                           'sha256': hashlib.sha256((baseline/'saved-matrix-audit.json').read_bytes()).hexdigest()},
        'contract': write(tmp_path/'contract.json', {'created_at': '2026-11-01T00:00:00Z',
                                                   'provider': 'method', 'jobs': [], 'cases': 0})}
    if defect == 'downgrade':
        spec['missing'][0]['status'] = 'not_called'
        receipt['state'] = 'pre_call_blocked_no_method_calls'
        receipt['native_route_receipt'] = {'transport_state': 'not_started'}
    elif defect == 'receipt':
        receipt['elapsed_ms'] = 123
    spec['missing'][0]['receipt'] = write(tmp_path/'new-receipt.json', receipt)
    if defect:
        with pytest.raises(ValueError, match='retained invocation state or receipt'):
            build(tmp_path, spec)
    else:
        assert check_saved_matrix(build(tmp_path, spec).parent)['valid']


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


@pytest.mark.parametrize('execution_defect', [None, 'missing_policy', 'missing_native'])
def test_superseded_evidence_preserved_without_double_counting(tmp_path, monkeypatch, execution_defect):
    if execution_defect != 'missing_native':
        monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: None)
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
    if execution_defect != 'missing_policy':
        scope['execution_contract'] = write(tmp_path/'execution-contract.json', {
            'created_at': '2026-10-28T00:00:00Z', 'provider_config_sha256': 'a'*64,
            'timeout_seconds': 30, 'max_failure_retries': 0})
    spec['supersession_scope'] = write(tmp_path/'scope.json', scope)
    if execution_defect:
        with pytest.raises(ValueError, match='execution authorization|execution contract'):
            build(tmp_path, spec)
        return
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
    assert check_saved_matrix(entry.parent, spec_path=tmp_path/'spec.json')['valid']
    assert_provenance_mutation_rejected(entry.parent, history['record_path'], tmp_path/'spec.json')
    assert_redirected_answer_rejected(entry.parent, ['history', 0, 'answer_path'], tmp_path/'spec.json')
    assert_proof_mutations_rejected(entry.parent, tmp_path/'spec.json', 'history', ['redirect_scope'])
    for timestamp in (None, '2026-10-30T00:00:00Z'):
        invalid = deepcopy(spec)
        invalid['supersession_scope'] = write(tmp_path/'late-scope.json', {**scope, 'created_at': timestamp})
        with pytest.raises(ValueError, match='timestamp|predates'):
            build(tmp_path, invalid, 'late-scope-' + str(timestamp))
    spec['supersession_scope'] = write(tmp_path/'wrong-scope.json', {**scope,'cells':[]})
    with pytest.raises(ValueError, match='complete replacement scope'):
        build(tmp_path,spec,'invalid-scope')


@pytest.mark.parametrize('defect', [None, 'missing_policy', 'late_policy', 'wrong_config', 'budget',
                                     'missing_observations', 'missing_log', 'log_config', 'log_count',
                                     'log_before_scope', 'receipt_chronology'])
def test_uncommitted_replacement_retains_old_history_without_score(tmp_path, defect):
    spec, root, old, raw = fixture(tmp_path)
    spec['superseded_sources'] = spec.pop('sources')
    spec['sources'] = []
    old_file = root/'old-record.json'
    old_file.write_bytes(raw)
    spec['supersession_scope'] = write(tmp_path/'scope.json', {
        'kind':'predeclared_replacement_scope', 'created_at':'2026-10-28T00:00:00Z', 'cells':[{
            'suite':'first','provider':'method','case_id':'same',
            'old_record':{'path':str(old_file),'sha256':hashlib.sha256(raw).hexdigest()}}]})
    scope = json.loads(Path(spec['supersession_scope']['path']).read_text())
    if defect != 'missing_policy':
        scope['execution_contract'] = write(tmp_path/'execution-contract.json', {
            'created_at': '2026-10-30T00:00:00Z' if defect == 'late_policy' else '2026-10-28T00:00:00Z',
            'provider_config_sha256': 'a'*64, 'timeout_seconds': 30, 'max_failure_retries': 1})
    if defect == 'log_before_scope':
        scope['created_at'] = '2026-10-30T00:00:00Z'
    spec['supersession_scope'] = write(tmp_path/'scope.json', scope)
    diagnostic = write(tmp_path/'diagnostic.json', {'observed_http_status':502})
    receipt = write(tmp_path/'receipt.json', {'suite':'first','provider':'method',
        'case_id':'same','state':'called_but_uncommitted','elapsed_ms':None,'started_at':'2026-10-29T00:00:00Z'})
    spec['missing'] = [{'suite':'first','provider':'method','case_id':'same',
        'status':'uncommitted','reason':'Collection error after HTTP 502',
        'receipt':receipt,'diagnostic_sources':[diagnostic]}]
    value = json.loads(Path(receipt['path']).read_text())
    if defect != 'missing_observations':
        value.update(provider_config_sha256='a'*64, timeout_seconds=30, physical_attempts=1)
    if defect != 'missing_log':
        log = tmp_path/'execution.jsonl'
        event = {'event': 'http_request_started', 'provider': 'method', 'case_id': 'same',
                 'started_at': '2026-10-29T00:00:00Z', 'timeout_seconds': 30,
                 'provider_config_sha256': 'b'*64 if defect == 'log_config' else 'a'*64}
        log.write_text((json.dumps(event)+'\n')*(2 if defect == 'log_count' else 1))
        value['execution_log'] = {'path': str(log), 'sha256': hashlib.sha256(log.read_bytes()).hexdigest()}
    if defect in {'log_before_scope', 'receipt_chronology'}:
        value['started_at'] = '2026-10-31T00:00:00Z'
    spec['missing'][0]['receipt'] = write(Path(receipt['path']), value)
    if defect in {'wrong_config', 'budget'}:
        value = json.loads(Path(receipt['path']).read_text())
        value.update(provider_config_sha256='b'*64 if defect == 'wrong_config' else 'a'*64,
                     physical_attempts=3 if defect == 'budget' else 1)
        spec['missing'][0]['receipt'] = write(Path(receipt['path']), value)
    if defect:
        with pytest.raises(ValueError, match='execution authorization'):
            build(tmp_path, spec)
        return
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
def test_retry_continuation_keeps_final_verdict_and_prior_bytes(tmp_path, final_pass, prior_pass, monkeypatch):
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
    assert_proof_mutations_rejected(entry.parent, tmp_path/'spec.json', 'retry_continuation',
                                    ['remove', 'total_physical_calls', 'execution_cost_ms'])
    continuation=row['retry_continuation']
    assert continuation['total_physical_calls']==2 and continuation['execution_cost_ms']==20.0
    assert (entry.parent/continuation['prior_record_path']).read_bytes()==old_raw
    assert (entry.parent/row['record_path']).read_bytes()==raw
    assert 'retry-continuation' in (entry.parent/'attempts/first--run-001.html').read_text()
    assert 'Synthetic retry disclosure.' in (entry.parent/'index.html').read_text()
    assert check_saved_matrix(entry.parent)['valid']
    assert_provenance_mutation_rejected(entry.parent, continuation['prior_record_path'], tmp_path/'spec.json')
    assert_redirected_answer_rejected(entry.parent, ['retry_continuation', 'prior_answer_path'], tmp_path/'spec.json')
    monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: None)
    for late_scope in (False, True):
        replacement = deepcopy(spec)
        replacement['superseded_sources'] = [prior_source]
        replacement['supersession_scope'] = write(tmp_path/f'scope-{late_scope}.json', {
            'kind': 'predeclared_replacement_scope',
            'created_at': '2026-10-29T00:30:00Z' if late_scope else '2026-10-28T00:00:00Z',
            'execution_contract': write(tmp_path/'execution-contract.json', {
                'created_at': '2026-10-28T00:00:00Z', 'provider_config_sha256': 'a'*64,
                'timeout_seconds': 30, 'max_failure_retries': 1}),
            'cells': [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                       'old_record': prior_source['results']}]})
        if late_scope:
            with pytest.raises(ValueError, match='predates'):
                build(tmp_path, replacement, 'late-split-replacement')
        else:
            build(tmp_path, replacement, 'authorized-split-replacement')
    write(tmp_path/'spec.json', spec)
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


@pytest.mark.parametrize('defect', [None, 'missing_identity', 'unrelated', 'missing_time', 'post_launch',
                                  'out_of_order', 'early_completion', 'missing_event_time', 'missing_event_identity', 'other_event_identity'])
def test_retry_continuation_preserves_uncommitted_prior_without_invented_time(tmp_path, defect):
    spec, root, current, raw = fixture(tmp_path)
    event = {'event': 'http_request_started', 'call_id': 'method__same__r001', 'at': '2026-10-27T00:00:00Z'}
    if defect == 'missing_identity':
        event.pop('call_id')
    elif defect == 'unrelated':
        event['call_id'] = 'another__case__r001'
    elif defect == 'missing_time':
        event.pop('at')
    elif defect == 'post_launch':
        event['at'] = '2026-10-28T01:00:00Z'
    events = [event]
    complete = {'event': 'collection_completed', 'call_id': 'method__same__r001', 'at': '2026-10-27T00:00:01Z'}
    if defect == 'out_of_order':
        events = [complete, event]
    elif defect == 'early_completion':
        complete['at'] = '2026-10-26T23:59:59Z'
        events = [complete, event]
    elif defect == 'missing_event_time':
        complete.pop('at')
        events.append(complete)
    elif defect in ('missing_event_identity', 'other_event_identity'):
        if defect == 'missing_event_identity':
            complete.pop('call_id')
        else:
            complete['call_id'] = 'other__case__r001'
        events.append(complete)
    log_path = tmp_path/'first-log.jsonl'
    log_path.write_text(''.join(json.dumps(item)+'\n' for item in events))
    log = {'path': str(log_path), 'sha256': hashlib.sha256(log_path.read_bytes()).hexdigest()}
    receipt=write(tmp_path/'uncommitted.json', {'suite':'first','provider':'method','case_id':'same',
        'state':'called_but_uncommitted','elapsed_ms':None})
    launch=write(tmp_path/'launch.json', {'started_at':'2026-10-28T00:00:00Z',
        'case_ids':['same'],'provider':'method','total_authorized_physical_calls_per_case':2,
        'first_uncommitted':{'case_id':'same','sha256':log['sha256']}})
    spec['retry_continuations']=[{'suite':'first','provider':'method','case_id':'same',
        'launch_contract':launch,'current_record_sha256':hashlib.sha256(raw).hexdigest(),
        'prior_uncommitted':receipt,'prior_log':log}]
    if defect:
        with pytest.raises(ValueError, match='Uncommitted first-call log'):
            build(tmp_path, spec)
        return
    entry=build(tmp_path,spec)
    from harness.web_fetch.report_saved_matrix import _load_saved_matrix, _validate_call_sequence
    loaded = _load_saved_matrix(tmp_path/'spec.json')
    continuation = loaded['continuations'][('first', 'method', 'same')]
    policy = {'created_at': '2026-10-26T00:00:00Z', 'max_failure_retries': 1}
    _validate_call_sequence(current, {}, policy, continuation)
    failed = {**current, 'metrics': {**current['metrics'], 'content_pass': False}}
    _validate_call_sequence(failed, {}, policy, continuation)
    with pytest.raises(ValueError, match='exceeds retry allowance'):
        _validate_call_sequence(current, {}, {**policy, 'max_failure_retries': 0}, continuation)
    with pytest.raises(ValueError, match='predates its contract'):
        _validate_call_sequence(current, {}, {**policy, 'created_at': '2026-10-27T12:00:00Z'}, continuation)
    row=json.loads((entry.parent/'matrix.json').read_text())['rows'][0]
    assert row['passed'] is True and row['retry_continuation']['execution_cost_ms'] is None
    assert_proof_mutations_rejected(entry.parent, tmp_path/'spec.json', 'retry_continuation',
                                    ['remove', 'total_physical_calls', 'execution_cost_ms'])
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
def test_single_case_parameter_group_preserves_both_calls_and_selects_improvement(tmp_path, before, after, selected, monkeypatch):
    # Exercise group selection independently of native transport authorization.
    monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: None)
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
    assert_proof_mutations_rejected(report, tmp_path/'spec.json', 'parameter_rerun',
                                    ['remove', 'selected_new', 'group', 'before_passed', 'candidate_passed'])
    assert_saved_receipt_mutation_rejected(report, tmp_path/'spec.json')
    assert row['parameter_rerun']['selected_new'] is selected
    assert 'data-parameter-reruns' in (report/'index.html').read_text()
    assert 'data-parameter-history' in (report/'changes/index.html').read_text()
    assert row['passed'] is (before or after)
    actual = json.loads((report/row['record_path']).read_text())
    assert actual['attempt_id'] == ('new-run' if selected else 'run-001')
    for label in ('before','candidate'):
        assert (report/row['parameter_rerun'][label+'_record_path']).is_file()
    assert check_saved_matrix(report, spec_path=tmp_path/'spec.json')['valid']
    for label in ('before', 'candidate'):
        assert_provenance_mutation_rejected(report, row['parameter_rerun'][label+'_record_path'], tmp_path/'spec.json')
        assert_redirected_answer_rejected(report, ['parameter_rerun', label+'_answer_path'], tmp_path/'spec.json')
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


def test_parameter_rerun_parts_must_cover_the_whole_original_job(tmp_path, monkeypatch):
    monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: None)
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


@pytest.mark.parametrize('before,after,expected', [([True, False], [False, True], False),
                                                  ([False, True], [True, False], False)])
def test_presentation_exclusions_preserve_frozen_parameter_population(before, after, expected):
    from harness.web_fetch.report_saved_matrix import _parameter_group_decisions
    keys = [('main', 'method', 'a'), ('base', 'method', 'b')]
    def saved(passed):
        return (None, {'ok': True, 'metrics': {'content_pass': passed, 'request_ok': True}}, None, None)
    inputs = {'revision': None, 'cases': {'main': {'a': {'track': 'dynamic'}},
                                         'base': {'b': {'track': 'dynamic'}}},
              'spec': {'excluded_report_families': ['base-dynamic'], 'report_families': [
                  {'id': 'dynamic', 'source_suites': ['main']},
                  {'id': 'base-dynamic', 'task_family': 'dynamic', 'source_suites': ['base']}]},
              'selected': {key: saved(value) for key, value in zip(keys, before)}}
    decisions = _parameter_group_decisions(inputs, {key: saved(value) for key, value in zip(keys, after)})
    assert set(decisions) == set(keys)
    assert decisions[keys[0]]['count'] == 2
    assert decisions[keys[0]]['selected_new'] is expected


@pytest.mark.parametrize('final_pass', [False, True])
def test_parameter_candidate_view_retains_a_separately_completed_retry(tmp_path, final_pass, monkeypatch):
    monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: None)
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
    ('a'*64, 'a'*64, True), ('a'*64, 'b'*64, False), (None, 'b'*64, False),
    (None, None, False), ('', '', False), ('same', 'same', False)])
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


@pytest.mark.parametrize('defect', [None, 'budget', 'source'])
def test_source_bound_retry_launch_keeps_its_original_contract(tmp_path, defect):
    spec, root, final, _ = fixture(tmp_path)
    prior = deepcopy(final)
    prior.update(ok=False, started_at='2026-10-28T00:00:00Z')
    prior['metrics'].update(content_pass=False, request_ok=False)
    prior_path = root/'prior.jsonl'
    prior_binding = write(prior_path, prior)
    final['started_at'] = '2026-10-29T00:00:00Z'
    final_binding = write(root/'results.jsonl', final)
    spec['sources'][0]['results'] = final_binding
    launch = {'started_at': '2026-10-28T01:00:00Z', 'case_ids': ['same'],
              'continuation_kind': 'remaining_one_retry', 'max_failure_retries': 0,
              'prior_physical_calls': {'same': 1}, 'prior_sources': {'same': str(root)}}
    if defect == 'budget': launch['max_failure_retries'] = 1
    if defect == 'source': launch['prior_sources']['same'] = str(tmp_path)
    spec['retry_continuations'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
        'current_record_sha256': final_binding['sha256'], 'launch_contract': write(tmp_path/'launch.json', launch),
        'prior_source': {'root': str(root), 'results': prior_binding, 'dataset': spec['suites'][0]['dataset']}}]
    if defect:
        with pytest.raises(ValueError, match='Retry continuation'):
            build(tmp_path, spec)
        return
    output = build(tmp_path, spec).parent
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    assert row['passed'] and row['retries'] == 1
    assert row['retry_continuation']['total_physical_calls'] == 2
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']


@pytest.mark.parametrize('defect', [None, 'scope', 'method', 'task', 'missing_execution', 'overlap', 'uncovered'])
def test_explicit_group_adoption_keeps_regressions_and_other_groups(tmp_path, defect, monkeypatch):
    monkeypatch.setattr('harness.web_fetch.report_saved_matrix._parameter_execution', lambda *args: None)
    spec, source, before, before_raw = fixture(tmp_path)
    spec['suites'].append({**deepcopy(spec['suites'][0]), 'id': 'second', 'label': 'Unchanged'})
    spec['sources'].append({**deepcopy(spec['sources'][0]), 'suite': 'second'})
    spec['report_families'] = [
        {'id': 'web', 'label': 'Chosen', 'source_suites': ['first']},
        {'id': 'other', 'task_family': 'web', 'label': 'Unchanged', 'source_suites': ['second']}]
    nested, candidate, after, _ = fixture(tmp_path/'candidate')
    after.update(ok=False, elapsed_ms=99, started_at='2026-11-01T00:00:00Z')
    after['metrics'].update(content_pass=False, request_ok=False)
    if defect == 'task':
        data = json.loads((candidate/'dataset.json').read_text())
        data['cases'][0]['url'] = after['url'] = 'https://different.example/'
        nested['suites'][0]['dataset'] = write(candidate/'dataset.json', data)
    nested['sources'][0]['results'] = write(candidate/'results.jsonl', after)
    if defect == 'scope':
        nested['suites'].append({**deepcopy(nested['suites'][0]), 'id': 'second', 'label': 'Extra'})
        nested['sources'].append({**deepcopy(nested['sources'][0]), 'suite': 'second'})
    authority = {'kind': 'user_selected_complete_report_groups',
                 'description': 'Adopt this complete group, including failures.',
                 'groups': [{'provider': 'unknown' if defect == 'method' else 'method', 'family': 'web'}]}
    spec['adoptions'] = [{'authority': write(tmp_path/'authority.json', authority),
                         'saved_matrix_spec': write(tmp_path/'candidate/spec.json', nested)}]
    contract = write(tmp_path/'execution-contract.json', {'created_at': '2026-10-01T00:00:00Z',
        'provider_config_sha256': 'a'*64, 'timeout_seconds': 30, 'max_failure_retries': 0})
    if defect != 'missing_execution':
        spec['adoptions'][0]['execution_contracts'] = [{'suite': 'first', 'provider': 'method',
            'case_ids': [] if defect == 'uncovered' else ['same'], 'contract': contract}]
    if defect == 'overlap':
        spec['adoptions'][0]['execution_contract'] = contract
    if defect:
        with pytest.raises(ValueError, match='Adoption'):
            build(tmp_path, spec)
        return
    output = build(tmp_path, spec).parent
    rows = json.loads((output/'matrix.json').read_text())['rows']
    chosen, retained = rows
    assert chosen['passed'] is False and chosen['elapsed_ms'] == 99
    assert (output/chosen['record_path']).read_bytes() == (candidate/'results.jsonl').read_bytes()
    assert (output/chosen['history'][0]['record_path']).read_bytes() == before_raw
    assert retained['passed'] and retained['elapsed_ms'] == 12
    assert (output/retained['record_path']).read_bytes() == before_raw
    assert (source/'results.jsonl').read_bytes() == before_raw
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('early_kind', ['blocked', 'skip'])
@pytest.mark.parametrize('later_kind', ['uncommitted', 'started', 'event', 'inherited_event', 'diagnostic', 'scoped_diagnostic', 'nested_native', 'nested_event', 'conflicting_diagnostic'])
def test_source_admission_rejects_not_called_with_invocation_evidence(tmp_path, reverse, early_kind, later_kind):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] = []
    identity = {'suite': 'first', 'provider': 'method', 'case_id': 'same'}
    early = {**identity, 'state': 'pre_call_blocked_no_method_calls',
             'native_route_receipt': {'transport_state': 'not_started'}}
    if early_kind == 'skip':
        early = {**identity, 'kind': 'user_requested_case_skip', 'providers': ['method'],
                 'status': 'not_called', 'score': None, 'user_instruction': 'Skip this task.'}
    late = {**identity, 'state': 'called_but_uncommitted'} if later_kind == 'uncommitted' else {
        **identity, 'native_route_receipt': {'transport_state': 'started'}}
    if later_kind == 'event':
        late = {**identity, 'event': 'http_request_started'}
    elif later_kind == 'inherited_event':
        late = {**identity, 'events': [{'event': 'http_request_started'}]}
    if later_kind in ('diagnostic', 'scoped_diagnostic'):
        late = {'call_id': 'method__same__r001', 'at': '2026-10-27T00:00:00Z',
                'source': 'http', 'event': 'http_request_started'}
    if later_kind in ('nested_native', 'nested_event', 'conflicting_diagnostic'):
        late = {'call_id': 'method__same__r001'}
        if later_kind == 'nested_native':
            late['native_route_receipt'] = {'transport_state': 'started'}
        else:
            late['events'] = [{'event': 'http_request_started'}]
            if later_kind == 'conflicting_diagnostic':
                late['events'][0]['call_id'] = 'other__case__r001'
    events = [late, early] if reverse else [early, late]
    if later_kind == 'scoped_diagnostic':
        events = {'suite': 'first', 'events': events}
    spec['missing'] = [{**identity, 'status': 'not_called', 'reason': 'Claimed skip',
                        'receipt': write(tmp_path/'receipt.json', events)}]
    with pytest.raises(ValueError):
        build(tmp_path, spec)


def assert_revision_document_mutation_rejected(output, spec, relative):
    path, audit_path = output/relative, output/'saved-matrix-audit.json'
    original, original_audit = path.read_bytes(), audit_path.read_bytes()
    value = json.loads(original)
    if isinstance(value, list):
        value.append({'reason': 'invented review decision'})
    else:
        value['invented_revision_history'] = True
    digest = write(path, value)['sha256']
    audit = json.loads(original_audit)
    audit['files'][relative] = digest
    write(audit_path, audit)
    try:
        with pytest.raises(ValueError, match='revision (document|attachment) differs'):
            check_saved_matrix(output, spec_path=spec)
    finally:
        path.write_bytes(original)
        audit_path.write_bytes(original_audit)


def test_source_checker_binds_report_family_configuration(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['suites'].append({**spec['suites'][0], 'id': 'second', 'label': 'Second'})
    spec['sources'].append({**spec['sources'][0], 'suite': 'second'})
    spec['report_families'] = [{'id': name, 'task_family': 'web', 'label': name, 'source_suites': [suite]}
                              for name, suite in [('web-a', 'first'), ('web-b', 'second')]]
    output = build(tmp_path, spec).parent
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'report_families',
                                    ['remove', 'change'], error='configuration differs')


def assert_saved_receipt_mutation_rejected(output, spec):
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    path, audit_path = output/row['receipt_path'], output/'saved-matrix-audit.json'
    original, original_audit = path.read_bytes(), audit_path.read_bytes()
    audit = json.loads(original_audit)
    audit['files'][row['receipt_path']] = write(path, {})['sha256']
    write(audit_path, audit)
    try:
        with pytest.raises(ValueError, match='Saved result receipt differs'):
            check_saved_matrix(output, spec_path=spec)
    finally:
        path.write_bytes(original)
        audit_path.write_bytes(original_audit)


def test_source_checker_requires_saved_receipt_contents(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    assert_saved_receipt_mutation_rejected(output, tmp_path/'spec.json')


def assert_whitespace_record_rejected(output, relative, spec):
    path, audit_path = output/relative, output/'saved-matrix-audit.json'
    original, original_audit = path.read_bytes(), audit_path.read_bytes()
    path.write_bytes(b' '+original)
    audit = json.loads(original_audit)
    audit['files'][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(audit_path, audit)
    try:
        with pytest.raises(ValueError, match='result receipt differs|provenance record differs'):
            check_saved_matrix(output, spec_path=spec)
    finally:
        path.write_bytes(original)
        audit_path.write_bytes(original_audit)


def test_selected_record_bytes_match_source_receipt(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    row = json.loads((output/'matrix.json').read_text())['rows'][0]
    assert_whitespace_record_rejected(output, row['record_path'], tmp_path/'spec.json')


def test_missing_reason_is_bound_to_source(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] = []
    identity = {'suite': 'first', 'provider': 'method', 'case_id': 'same'}
    spec['missing'] = [{**identity, 'status': 'uncommitted', 'reason': 'Original collection error',
        'receipt': write(tmp_path/'receipt.json', {**identity, 'state': 'called_but_uncommitted'})}]
    output = build(tmp_path, spec).parent
    assert_proof_mutations_rejected(output, tmp_path/'spec.json', 'error', ['change_note'],
                                    error='missing receipt differs from validated source')


def assert_scoring_flags_rejected(output, spec):
    audit_path = output/'saved-matrix-audit.json'
    for name in ('saved-matrix-audit.json', 'matrix.json', 'execution.json'):
        path = output/name
        original, original_audit = path.read_bytes(), audit_path.read_bytes()
        value = json.loads(original)
        value['scores_changed'] = not value['scores_changed']
        digest = write(path, value)['sha256']
        if name != 'saved-matrix-audit.json':
            audit = json.loads(original_audit)
            audit['files'][name] = digest
            write(audit_path, audit)
        try:
            for source in (None, spec):
                with pytest.raises(ValueError, match='scoring-change flags differ'):
                    check_saved_matrix(output, spec_path=source)
        finally:
            path.write_bytes(original)
            audit_path.write_bytes(original_audit)


@pytest.mark.parametrize('change', ['verdict', 'latency', 'explanation'])
def test_source_checker_rejects_rendered_content_mutation(tmp_path, change):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']
    path = output/'attempts/first--run-001.html'
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(path.read_text(), 'html.parser')
    heading = soup.find('h1')
    assert heading is not None
    heading.string = {'verdict': 'Failed instead of passed', 'latency': '999999 ms',
                      'explanation': 'Different failure explanation'}[change]
    path.write_text(str(soup))
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files'][path.relative_to(output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='Rendered page differs'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json')


@pytest.mark.parametrize('extension', ['js', 'css'])
def test_source_checker_rejects_modified_executable_assets(tmp_path, extension):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    asset = next((output/'assets').glob('*.'+extension))
    asset.write_text(asset.read_text()+'\n/* changed presentation */\n')
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files'][asset.relative_to(output).as_posix()] = hashlib.sha256(asset.read_bytes()).hexdigest()
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='Rendered asset differs'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json')


def test_source_render_does_not_trust_unbound_release_metadata(tmp_path):
    from harness.web_fetch.report import generate_saved_matrix
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    model = json.loads((output/'matrix.json').read_text())
    write(output/'benchmark-release.json', {'version': 'invented-release'})
    for name in ('dataset.json', 'summary.json', 'execution.json', 'results.jsonl', 'en/index.html'):
        (output/name).unlink()
    generate_saved_matrix(output, model)
    assert 'invented-release' in (output/'index.html').read_text()
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files'] = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in output.rglob('*') if p.is_file() and p.name != 'saved-matrix-audit.json'}
    write(output/'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='Rendered page differs'):
        check_saved_matrix(output, spec_path=tmp_path/'spec.json')


@pytest.mark.parametrize('encoded_target', [True, False])
def test_receipt_rejects_same_node_identity_conflict(encoded_target):
    from harness.web_fetch.report_saved_matrix import _receipt_covers
    value = {'suite': 'first', 'provider': 'other', 'case_id': 'different',
             'call_id': 'method__same__r001', 'state': 'called_but_uncommitted'}
    provider, case = ('method', 'same') if encoded_target else ('other', 'different')
    with pytest.raises(ValueError, match='Conflicting diagnostic'):
        _receipt_covers(value, 'first', provider, case, 'uncommitted')


@pytest.mark.parametrize('jsonl', [False, True])
@pytest.mark.parametrize('matching', [False, True])
def test_missing_receipt_reconciles_separate_diagnostics(tmp_path, jsonl, matching):
    spec, _, _, _ = fixture(tmp_path)
    spec['sources'] = []
    identity = {'suite': 'first', 'provider': 'method', 'case_id': 'same'}
    receipt = write(tmp_path/'skip.json', {**identity, 'kind': 'user_requested_case_skip',
        'providers': ['method'], 'status': 'not_called', 'score': None,
        'user_instruction': 'Skip this task.'})
    events = [{'call_id': 'method__same__r001' if matching else 'other__case__r001',
               'event': 'http_request_started'}, {'event': 'collection_completed'}]
    path = tmp_path/('diagnostic.jsonl' if jsonl else 'diagnostic.json')
    if jsonl:
        path.write_text(''.join(json.dumps(event)+'\n' for event in events))
        diagnostic = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    else:
        diagnostic = write(path, events)
    spec['missing'] = [{**identity, 'status': 'not_called', 'reason': 'Requested skip',
                        'receipt': receipt, 'diagnostic_sources': [diagnostic]}]
    if matching:
        with pytest.raises(ValueError, match='Missing receipt'):
            build(tmp_path, spec)
    else:
        build(tmp_path, spec)


@pytest.mark.parametrize('field,value', [('name', 'Different task'), ('url', 'https://unrelated.example/')])
def test_matrix_task_metadata_matches_case_evidence(tmp_path, field, value):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    model = json.loads((output/'matrix.json').read_text())
    model['rows'][0][field] = value
    audit = json.loads((output/'saved-matrix-audit.json').read_text())
    audit['files']['matrix.json'] = write(output/'matrix.json', model)['sha256']
    write(output/'saved-matrix-audit.json', audit)
    for source in (None, tmp_path/'spec.json'):
        with pytest.raises(ValueError, match='Saved case metadata differs from matrix'):
            check_saved_matrix(output, spec_path=source)


@pytest.mark.parametrize('allowance', [None, -1, True, '1', 1.0])
def test_call_sequence_rejects_missing_or_invalid_retry_authorization(allowance):
    from harness.web_fetch.report_saved_matrix import _validate_call_sequence
    with pytest.raises(ValueError, match='explicit nonnegative retry allowance'):
        _validate_call_sequence({}, {}, {'max_failure_retries': allowance})


@pytest.mark.parametrize('defect', [None, 'missing_count', 'missing_retries', 'contradictory_verdict'])
def test_call_sequence_requires_consistent_retry_evidence(defect):
    from harness.web_fetch.report_saved_matrix import _validate_call_sequence
    record = {'started_at': '2026-10-31T01:00:00Z', 'ok': True, 'elapsed_ms': 10,
              'metrics': {'request_ok': True, 'content_pass': True, 'content_sha256': 'a' * 64}}
    call = {**record, 'task_pass': True, 'content_sha256': 'a' * 64}
    retry = {'physical_attempts': 1, 'retries_used': 0, 'attempts': [call]}
    record['provider_metadata'] = {'retry': retry}
    contract = {'created_at': '2026-10-31T00:00:00Z', 'max_failure_retries': 1}
    if defect == 'missing_count':
        del retry['physical_attempts']
    elif defect == 'missing_retries':
        del retry['retries_used']
    elif defect == 'contradictory_verdict':
        call['task_pass'] = False
    if defect:
        with pytest.raises(ValueError, match='physical call counts|verdict differs'):
            _validate_call_sequence(record, {}, contract)
    else:
        _validate_call_sequence(record, {}, contract)


def test_explicit_zero_retry_contract_accepts_one_failed_call():
    from harness.web_fetch.report_saved_matrix import _validate_call_sequence
    record = {'started_at': '2026-10-31T01:00:00Z', 'ok': True,
              'metrics': {'request_ok': True, 'content_pass': False}}
    _validate_call_sequence(record, {}, {
        'created_at': '2026-10-31T00:00:00Z', 'max_failure_retries': 0})


def test_saved_matrix_binds_external_policy_registry_and_rejects_substitution(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    registry = {'schema_version': 1, 'sources': [
        {'source_sha256': 'a' * 64, 'version': 2, 'reason': 'Synthetic historical approval'}]}
    spec['native_policy_registry'] = write(tmp_path / 'approved-policies.json', registry)
    report = build(tmp_path, spec).parent
    model = json.loads((report / 'matrix.json').read_text())
    assert model['native_policy_registry']['sha256'] == spec['native_policy_registry']['sha256']
    assert json.loads((report / model['native_policy_registry']['path']).read_text()) == registry
    assert check_saved_matrix(report, spec_path=tmp_path / 'spec.json')['valid']
    (tmp_path / 'approved-policies.json').write_text('{}')
    with pytest.raises(ValueError, match='differs from its approved binding'):
        check_saved_matrix(report, spec_path=tmp_path / 'spec.json')
    (report / model['native_policy_registry']['path']).write_text('{}')
    with pytest.raises(ValueError, match='differs from its approved binding'):
        check_saved_matrix(report)


def test_final_saved_report_preserves_results_without_audit_process_banners(tmp_path):
    spec, _, _, raw = fixture(tmp_path)
    spec['presentation'] = 'final'
    report = build(tmp_path, spec).parent
    model = json.loads((report / 'matrix.json').read_text())
    assert model['totals']['planned'] == model['totals']['passed'] == 1
    assert (report / model['rows'][0]['record_path']).read_bytes() == raw
    html = (report / 'index.html').read_text()
    assert '本地审核版' not in html
    assert '本报告未正式发布' not in html
    assert 'data-saved-completion' not in html
    assert 'report_invocation_status' not in html
    assert check_saved_matrix(report, spec_path=tmp_path / 'spec.json')['valid']


def test_final_saved_report_rejects_uncalled_positions(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    spec['presentation'] = 'final'
    spec['sources'] = []
    receipt = {'kind': 'user_requested_case_skip', 'suite': 'first', 'case_id': 'same',
               'providers': ['method'], 'status': 'not_called', 'score': None,
               'user_instruction': 'Skip this case.'}
    spec['missing'] = [{'suite': 'first', 'provider': 'method', 'case_id': 'same',
                        'status': 'not_called', 'reason': 'User requested skip',
                        'receipt': write(tmp_path / 'skip.json', receipt)}]
    with pytest.raises(ValueError, match='complete judged call evidence'):
        build(tmp_path, spec)
