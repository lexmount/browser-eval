import hashlib
import json

import pytest

from harness.web_fetch.local_revision import require_local_revision, revise_local_snapshot
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed


@pytest.mark.parametrize('controls', [
    ['native-title-must-match-original'],
    ['native-title-must-match-original', 'ordinary-text-player-control-only'],
    ['native-title-must-match-original', 'unknown-source-control'],
    [],
])
def test_revision_controls_resolve_in_their_actual_owner(controls):
    from harness.web_fetch.local_revision import _require_revision_regressions
    if not controls or 'unknown-source-control' in controls:
        with pytest.raises(ValueError):
            _require_revision_regressions(controls)
    else:
        assert _require_revision_regressions(controls) == controls


@pytest.mark.parametrize('defect', [None, 'case', 'row', 'source', 'answer', 'context', 'native'])
def test_current_authority_survives_history_deletion_but_rejects_current_changes(tmp_path, defect):
    import shutil
    from harness.web_fetch.local_revision import compact_local_snapshot
    raw = b'Required article'
    (tmp_path/'answer.txt').write_bytes(raw)
    (tmp_path/'native.json').write_text('{"answer":"Required article"}')
    native = (tmp_path/'native.json').read_bytes()
    case = {'id': 'a', 'url': 'https://example.test/', 'required_facts': [
        {'id': 'article', 'alternatives': ['Required article']}]}
    row = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
        'artifact_path': 'answer.txt', 'elapsed_ms': 7,
        'provider_metadata': {'raw_response_path': 'native.json',
            'raw_response_sha256': hashlib.sha256(native).hexdigest(), 'raw_response_bytes': len(native)},
        'metrics': score_attempt(case, raw, ok=True)}
    other = {**row, 'attempt_id': 'q__a', 'provider': 'q',
             'metrics': {**row['metrics'], 'scoring_source_sha256': '1'*64}}
    dataset = {'cases': [case], 'comparison': {}}
    (tmp_path/'dataset.json').write_text(json.dumps(dataset))
    (tmp_path/'results.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in [row, other]))
    config = tmp_path/'config.json'
    config.write_text(json.dumps({'cases': [], 'rescore_attempt_ids': ['p__a'],
        'authority': {'description': 'Shared parser correction'},
        'regression_ids': ['ordinary-text-player-control-only', 'ordinary-text-with-player-control']}))
    revise_local_snapshot(tmp_path, config)
    path = next((tmp_path/'local-evidence/offline-revisions').glob('*/receipt.json'))
    old_binding = {'path': path.relative_to(tmp_path).as_posix(),
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    binding = compact_local_snapshot(tmp_path, old_binding)
    for directory in (tmp_path/'local-evidence/offline-revisions').iterdir():
        if directory.name != 'current':
            shutil.rmtree(directory)
    dataset = json.loads((tmp_path/'dataset.json').read_text())
    rows = [json.loads(line) for line in (tmp_path/'results.jsonl').read_text().splitlines()]
    expected = require_local_revision(tmp_path, binding, dataset, rows)
    assert expected['q__a'] == '1'*64
    if defect == 'case':
        dataset['cases'][0]['required_facts'] = ['Changed requirement']
    elif defect == 'row':
        rows[0]['elapsed_ms'] = 9
    elif defect == 'source':
        rows[0]['metrics']['scoring_source_sha256'] = '2'*64
    elif defect == 'context':
        rows[0]['ok'] = False
    elif defect == 'answer':
        (tmp_path/'answer.txt').write_text('Replaced answer')
    elif defect == 'native':
        (tmp_path/'native.json').write_text('{}')
    if defect:
        (tmp_path/'dataset.json').write_text(json.dumps(dataset))
        (tmp_path/'results.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        authority_path = tmp_path/binding['path']
        authority = json.loads(authority_path.read_text())
        authority['dataset_sha256'] = hashlib.sha256((tmp_path/'dataset.json').read_bytes()).hexdigest()
        authority['results_sha256'] = hashlib.sha256((tmp_path/'results.jsonl').read_bytes()).hexdigest()
        authority_path.write_text(json.dumps(authority))
        binding['sha256'] = hashlib.sha256(authority_path.read_bytes()).hexdigest()
        with pytest.raises(ValueError):
            require_local_revision(tmp_path, binding, dataset, rows)


def test_offline_revision_preserves_calls_and_rejects_tampering(tmp_path):
    raw = b'0:00\n/\nLive\nWatch full video'
    (tmp_path / 'answer.txt').write_bytes(raw)
    case = {'id': 'a', 'url': 'https://example.test/', 'task_kind': 'structure',
            'content_score_applicable': False,
            'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'stable_text_anchors': [{'id': 'live', 'alternatives': ['Live']}]}}
    original = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
                'artifact_path': 'answer.txt', 'elapsed_ms': 7,
                'metrics': score_attempt(case, raw, ok=True)}
    assert task_passed(True, original['metrics'])
    (tmp_path / 'dataset.json').write_text(json.dumps({'cases': [case], 'comparison': {}}))
    (tmp_path / 'results.jsonl').write_text(json.dumps(original) + '\n')
    changed = {**case, 'acceptance_contract': {**case['acceptance_contract'],
        'text_exclusions': {'text_patterns': [r'^\s*\d+:\d{2}\s*/\s*Live\s*Watch full video\b']}}}
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'cases': [changed], 'rule_revision': True, 'authority': {'description': 'Retain ordinary page text requirement'},
        'regression_ids': ['ordinary-text-player-control-only', 'ordinary-text-with-player-control']}))
    invalid = json.loads(config.read_text())
    invalid['rule_revision'] = False
    config.write_text(json.dumps(invalid))
    with pytest.raises(ValueError, match='explicit rule revision'):
        revise_local_snapshot(tmp_path, config)
    invalid['rule_revision'] = True
    config.write_text(json.dumps(invalid))
    receipt = revise_local_snapshot(tmp_path, config)
    assert receipt['provider_calls_added'] == 0
    assert receipt['rule_revision'] is True
    assert receipt['task_verdict_changes'] == [{'attempt_id': 'p__a', 'before': True, 'after': False}]
    assert (tmp_path / 'answer.txt').read_bytes() == raw
    rows = [json.loads((tmp_path / 'results.jsonl').read_text())]
    assert rows[0]['scoring']['scored_at']
    dataset = json.loads((tmp_path / 'dataset.json').read_text())
    path = next((tmp_path / 'local-evidence/offline-revisions').glob('*/receipt.json'))
    binding = {'path': str(path.relative_to(tmp_path)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    require_local_revision(tmp_path, binding, dataset, rows)
    # A self-consistent changed result hash does not authorize changing a call.
    rows[0]['elapsed_ms'] = 8
    (tmp_path / 'results.jsonl').write_text(json.dumps(rows[0]) + '\n')
    receipt['output_results_sha256'] = hashlib.sha256((tmp_path / 'results.jsonl').read_bytes()).hexdigest()
    path.write_text(json.dumps(receipt))
    binding['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='measured call facts'):
        require_local_revision(tmp_path, binding, dataset, rows)


def test_contract_revision_is_explicit_without_runtime_policy_switch(tmp_path):
    raw = b'Required body\nCAPTCHA'
    (tmp_path/'answer.txt').write_bytes(raw)
    case = {'id': 'a', 'url': 'https://example.test/', 'task_kind': 'body',
            'forbidden_any': ['CAPTCHA'], 'required_facts': [
                {'id': 'body', 'alternatives': ['Required body']}]}
    original = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
                'artifact_path': 'answer.txt', 'metrics': score_attempt(case, raw, ok=True)}
    assert not task_passed(True, original['metrics'])
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [case], 'comparison': {}}))
    (tmp_path/'results.jsonl').write_text(json.dumps(original)+'\n')
    changed = {k: v for k, v in case.items() if k != 'forbidden_any'}
    changed['diagnostic_any'] = ['CAPTCHA']
    config = tmp_path/'config.json'
    config.write_text(json.dumps({'cases': [changed], 'rule_revision': True,
        'authority': {'description': 'Explicit task revision: error words are diagnostic observations'},
        'regression_ids': ['declared-forbidden-token-fails', 'unforbidden-captcha-documentation-passes']}))
    receipt = revise_local_snapshot(tmp_path, config)
    assert receipt['rule_revision'] is True
    assert 'acceptance_policy_version' not in receipt
    rows = [json.loads((tmp_path/'results.jsonl').read_text())]
    assert 'acceptance_policy_version' not in rows[0]['metrics']
    assert task_passed(True, rows[0]['metrics'])


def test_same_input_keeps_distinct_revision_receipts(tmp_path):
    raw = b'Required article text. CAPTCHA'
    (tmp_path/'answer.txt').write_bytes(raw)
    case = {'id': 'a', 'url': 'https://example.test/', 'required_facts': [
        {'id': 'body', 'alternatives': ['Required article text']}], 'forbidden_any': ['CAPTCHA']}
    row = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
           'artifact_path': 'answer.txt', 'metrics': score_attempt(case, raw, ok=True)}
    original_dataset = json.dumps({'cases': [case], 'comparison': {}})
    original_results = json.dumps(row)+'\n'
    changed = {k: v for k, v in case.items() if k != 'forbidden_any'}
    changed['diagnostic_any'] = ['CAPTCHA']
    config = tmp_path/'config.json'
    preserved = None
    for reason in ['First declared correction', 'Second explicitly distinct revision']:
        (tmp_path/'dataset.json').write_text(original_dataset)
        (tmp_path/'results.jsonl').write_text(original_results)
        config.write_text(json.dumps({'cases': [changed], 'rule_revision': True,
            'authority': {'description': reason},
            'regression_ids': ['declared-forbidden-token-fails', 'unforbidden-captcha-documentation-passes']}))
        receipt = revise_local_snapshot(tmp_path, config)
        path = tmp_path/receipt['input_dataset']['path']
        if preserved is None:
            preserved = (path.parent/'receipt.json', (path.parent/'receipt.json').read_bytes())
    assert len(list((tmp_path/'local-evidence/offline-revisions').glob('*/receipt.json'))) == 2
    assert preserved[0].read_bytes() == preserved[1]


def _two_revision_chain(root):
    raw = b'Required body CAPTCHA'
    (root/'answer.txt').write_bytes(raw)
    case = {'id': 'a', 'url': 'https://example.test/', 'forbidden_any': ['CAPTCHA'],
            'required_facts': [{'id': 'body', 'alternatives': ['Required body']}]}
    row = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
           'artifact_path': 'answer.txt', 'elapsed_ms': 7,
           'metrics': score_attempt(case, raw, ok=True)}
    (root/'dataset.json').write_text(json.dumps({'cases': [case], 'comparison': {}}))
    (root/'results.jsonl').write_text(json.dumps(row)+'\n')
    changed = {k: v for k, v in case.items() if k != 'forbidden_any'}
    changed['diagnostic_any'] = ['CAPTCHA']
    config = root/'config.json'
    parent = None
    receipts = []
    for index in range(2):
        if index:
            changed = {**changed, 'required_facts': changed['required_facts'] + [
                {'id': 'additional', 'alternatives': ['Additional required body']}]}
        config.write_text(json.dumps({'cases': [changed], 'rule_revision': True,
            'authority': {'description': 'Declared correction ' + str(index)},
            'regression_ids': ['declared-forbidden-token-fails',
                               'unforbidden-captcha-documentation-passes'],
            **({'predecessor_revision': parent} if parent else {})}))
        receipt = revise_local_snapshot(root, config)
        path = root/receipt['input_dataset']['path']
        path = path.parent/'receipt.json'
        parent = {'path': str(path.relative_to(root)),
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        receipts.append((parent, receipt))
    return receipts


def test_score_revision_chain_validates_both_steps_and_reaches_original_inputs(tmp_path):
    from harness.web_fetch.local_revision import validate_revision_chain
    (first_binding, first), (latest_binding, latest) = _two_revision_chain(tmp_path)
    assert first['task_verdict_changes'] == [
        {'attempt_id': 'p__a', 'before': False, 'after': True}]
    assert latest['task_verdict_changes'] == [
        {'attempt_id': 'p__a', 'before': True, 'after': False}]
    original_bytes = (tmp_path/first_binding['path']).read_bytes()
    assert validate_revision_chain(tmp_path, latest_binding)[1:] == (
        first['input_dataset'], first['input_results'])
    assert (tmp_path/first_binding['path']).read_bytes() == original_bytes


@pytest.mark.parametrize('field,value', [
    ('authority', []),
    ('authority', {'description': 7}),
    ('input_dataset', None),
    ('input_results', []),
    ('changed_case_ids', [[]]),
    ('rescored_attempt_ids', 'all'),
    ('task_verdict_changes', [{'before': True, 'after': False}]),
    ('native_answer_corrections', [None]),
    ('native_context_corrections', [{'attempt_id': []}]),
    ('output_dataset_sha256', None),
    ('scoring_source_sha256', 1),
])
def test_revision_receipt_reports_malformed_fields_without_rewriting_evidence(tmp_path, field, value):
    from harness.web_fetch.local_revision import validate_revision_chain
    _, (binding, receipt) = _two_revision_chain(tmp_path)
    dataset_before = (tmp_path/'dataset.json').read_bytes()
    results_before = (tmp_path/'results.jsonl').read_bytes()
    receipt[field] = value
    path = tmp_path/binding['path']
    path.write_text(json.dumps(receipt))
    raw = path.read_bytes()
    binding['sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match='revision\\.' + field):
        validate_revision_chain(tmp_path, binding)
    assert path.read_bytes() == raw
    assert (tmp_path/'dataset.json').read_bytes() == dataset_before
    assert (tmp_path/'results.jsonl').read_bytes() == results_before


@pytest.mark.parametrize('tamper, message', [
    ('authority', 'authority'), ('regression_ids', 'regression provenance'),
    ('changed_case_ids', 'case scope'), ('task_verdict_changes', 'verdict transitions'),
    ('scoring_source_sha256', 'scoring source'),
    ('output_results_sha256', 'output binding'), ('measured_call', 'measured call facts'),
])
def test_hash_connected_intermediate_revision_must_pass_full_validation(tmp_path, tamper, message):
    from harness.web_fetch.local_revision import validate_revision_chain
    (parent_binding, parent), (latest_binding, latest) = _two_revision_chain(tmp_path)
    if tamper in {'authority', 'regression_ids'}:
        parent.pop(tamper)
    elif tamper in {'changed_case_ids', 'task_verdict_changes'}:
        parent[tamper] = []
    elif tamper == 'measured_call':
        old_path = tmp_path/parent['input_results']['path']
        old = json.loads(old_path.read_text())
        old['elapsed_ms'] = 99
        old_path.write_text(json.dumps(old)+'\n')
        parent['input_results']['sha256'] = hashlib.sha256(old_path.read_bytes()).hexdigest()
    else:
        parent[tamper] = '0'*64
    parent_path = tmp_path/parent_binding['path']
    parent_path.write_text(json.dumps(parent))
    latest['predecessor_revision']['sha256'] = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    latest_path = tmp_path/latest_binding['path']
    latest_path.write_text(json.dumps(latest))
    latest_binding['sha256'] = hashlib.sha256(latest_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=message):
        validate_revision_chain(tmp_path, latest_binding)[1:]


def _native_answer_revision_fixture(root):
    raw = b'Body fact'
    (root/'answer.txt').write_bytes(raw)
    native_content = 'Body fact | Saved to: /Users/person/private/page.txt'
    native = (json.dumps({'content': native_content})+'\n').encode()
    native_path = root/'private_artifacts/native.response.bin'
    native_path.parent.mkdir()
    native_path.write_bytes(native)
    case = {'id': 'a', 'url': 'https://example.test/',
            'required_facts': [{'id': 'body', 'alternatives': ['Body fact']}]}
    row = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
           'url': case['url'], 'artifact_path': 'answer.txt', 'elapsed_ms': 7,
           'diagnosis': {'category': 'obsolete diagnosis', 'evidence': 'old answer'},
           'failure_category': 'obsolete failure',
           'provider_metadata': {'raw_response_path': 'private_artifacts/native.response.bin',
               'raw_response_sha256': hashlib.sha256(native).hexdigest(),
               'raw_response_bytes': len(native)},
           'metrics': score_attempt(case, raw, ok=True)}
    (root/'dataset.json').write_text(json.dumps({'cases': [case], 'comparison': {}}))
    (root/'results.jsonl').write_text(json.dumps(row)+'\n')
    config = {'cases': [], 'authority': {'description': 'Restore declared native fields from the same call; preserve the former derived answer and measured time.'},
        'regression_ids': ['native-json-resource-declared-image-field-preserved',
                           'native-json-resource-untyped-url-echo-rejected'],
        'native_answer_corrections': [{'attempt_id': row['attempt_id'],
            'native_response': {'path': row['provider_metadata']['raw_response_path'],
                'sha256': row['provider_metadata']['raw_response_sha256']},
            'projection': {'schema_version': 1, 'content_pointer': ['content'],
                           'content_format': 'text'}}]}
    config_path = root/'correction.json'
    config_path.write_text(json.dumps(config))
    receipt = revise_local_snapshot(root, config_path)
    receipt_path = root/receipt['input_dataset']['path']
    receipt_path = receipt_path.parent/'receipt.json'
    binding = {'path': str(receipt_path.relative_to(root)),
               'sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
    return row, receipt, receipt_path, binding, native_content


def test_offline_native_correction_preserves_call_and_both_answers(tmp_path):
    original, receipt, _, binding, native_content = _native_answer_revision_fixture(tmp_path)
    current = json.loads((tmp_path/'results.jsonl').read_text())
    assert receipt['provider_calls_added'] == 0
    assert receipt['changed_case_ids'] == []
    assert receipt['task_verdict_changes'] == []
    assert current['elapsed_ms'] == original['elapsed_ms']
    assert 'diagnosis' not in current
    assert 'failure_category' not in current
    assert (tmp_path/'answer.txt').read_bytes() == b'Body fact'
    assert current['artifact_path'] != original['artifact_path']
    assert (tmp_path/current['artifact_path']).read_text() == native_content
    assert len(receipt['native_answer_corrections']) == 1
    require_local_revision(tmp_path, binding, json.loads((tmp_path/'dataset.json').read_text()), [current])


@pytest.mark.parametrize('failure', ['validation', 'install'])
def test_failed_native_revision_leaves_no_partial_files_and_can_retry(tmp_path, monkeypatch, failure):
    from pathlib import Path
    from harness.web_fetch import local_revision
    original_validate = local_revision.require_local_revision
    original_replace = Path.replace
    failed = False

    def validate(*args, **kwargs):
        if failure == 'validation':
            # The current snapshot is still untouched even at the final gate.
            row = json.loads((tmp_path/'results.jsonl').read_text())
            assert row['artifact_path'] == 'answer.txt'
            assert not list(tmp_path.glob('*-native-*'))
            raise ValueError('Injected validation rejection')
        return original_validate(*args, **kwargs)

    def replace(path, target):
        nonlocal failed
        if failure == 'install' and not failed and Path(target) == tmp_path/'results.jsonl':
            failed = True
            raise OSError('Injected installation failure')
        return original_replace(path, target)

    monkeypatch.setattr(local_revision, 'require_local_revision', validate)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises((ValueError, OSError), match='Injected'):
        _native_answer_revision_fixture(tmp_path)
    assert json.loads((tmp_path/'results.jsonl').read_text())['artifact_path'] == 'answer.txt'
    assert not list(tmp_path.glob('*-native-*'))
    assert not (tmp_path/'local-evidence').exists()
    assert not tmp_path.with_name('.'+tmp_path.name+'-revision').exists()
    monkeypatch.setattr(local_revision, 'require_local_revision', original_validate)
    monkeypatch.setattr(Path, 'replace', original_replace)
    receipt = revise_local_snapshot(tmp_path, tmp_path/'correction.json')
    assert receipt['provider_calls_added'] == 0
    assert len(receipt['native_answer_corrections']) == 1


def test_interrupted_install_is_recovered_before_next_revision(tmp_path):
    from harness.web_fetch.local_revision import _RevisionTransaction
    (tmp_path/'dataset.json').write_bytes(b'original')
    transaction = _RevisionTransaction(tmp_path)
    (transaction.work/'before').mkdir(parents=True)
    (transaction.work/'owner.json').write_text(json.dumps({
        'kind': 'local_revision_transaction', 'source': str(tmp_path.resolve())}))
    (transaction.work/'before/dataset.json').write_bytes(b'original')
    (transaction.work/'journal.json').write_text(json.dumps({
        'files': [{'path': 'dataset.json', 'existed': True},
                  {'path': 'partial/receipt.json', 'existed': False}],
        'directories': ['partial']}))
    (tmp_path/'dataset.json').write_bytes(b'incomplete')
    (tmp_path/'partial').mkdir()
    (tmp_path/'partial/receipt.json').write_bytes(b'partial')
    transaction.recover()
    assert (tmp_path/'dataset.json').read_bytes() == b'original'
    assert not (tmp_path/'partial').exists()
    assert not transaction.work.exists()


@pytest.mark.parametrize('source_kind', ['diagnostic', 'transport'])
def test_native_correction_apply_and_verify_share_same_call_url_evidence(tmp_path, source_kind):
    from harness.web_fetch.local_revision import _require_native_answer_correction
    original, _, _, _, _ = _native_answer_revision_fixture(tmp_path)
    row = json.loads((tmp_path/'results.jsonl').read_text())
    returned = 'https://returned.example.test/page'
    native = {'path': row['provider_metadata']['raw_response_path'],
              'sha256': row['provider_metadata']['raw_response_sha256']}
    if source_kind == 'diagnostic':
        raw = f'fetch request complete method=GET url={row["url"]} final_url={returned} status=200'.encode()
        (tmp_path/'diagnostic.txt').write_bytes(raw)
        row['provider_metadata'].update(diagnostic_log_path='diagnostic.txt',
            diagnostic_log_sha256=hashlib.sha256(raw).hexdigest())
        field = {'format': 'fetch_log_final_url'}
    else:
        row['provider_metadata']['source_transport_receipt'] = {'response_url': returned}
        field = {'format': 'transport_response_url'}
    (tmp_path/'results.jsonl').write_text(json.dumps(row)+'\n')
    declaration = {'schema_version': 2, 'content_format': 'text', 'content_pointer': ['content'],
                   'result_fields': {'final_url': field}}
    config = json.loads((tmp_path/'correction.json').read_text())
    config['native_answer_corrections'] = [{'attempt_id': row['attempt_id'],
        'native_response': native, 'projection': declaration}]
    path = tmp_path/'same-call.json'
    path.write_text(json.dumps(config))
    receipt = revise_local_snapshot(tmp_path, path)
    corrected = json.loads((tmp_path/'results.jsonl').read_text())
    assert corrected['final_url'] == returned
    assert corrected['elapsed_ms'] == original['elapsed_ms']
    _require_native_answer_correction(tmp_path, row, corrected, receipt['native_answer_corrections'][0])
    corrected['final_url'] = 'https://forged.example.test/'
    with pytest.raises(ValueError, match='context correction'):
        _require_native_answer_correction(tmp_path, row, corrected, receipt['native_answer_corrections'][0])


def test_native_correction_rejects_resource_base_url_before_install(tmp_path):
    _native_answer_revision_fixture(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    config = json.loads((tmp_path/'correction.json').read_text())
    config['native_answer_corrections'][0]['projection'].update(schema_version=2,
        answer_fields={'images': {'pointer': ['absent'], 'default': [],
            'format': 'html_attribute_urls', 'base_url': 'https://forged.example.test/'}})
    config_path = tmp_path/'wrong-base.json'
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='verified result or request URL'):
        revise_local_snapshot(tmp_path, config_path)
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file() and p != config_path}
    assert after == before


@pytest.mark.parametrize('defect,message', [
    ('clock', 'physical call facts'), ('metadata', 'unrelated response metadata'),
    ('answer', 'declared original fields'), ('source', 'original response'),
    ('missing_proof', 'measured call facts'),
])
def test_native_correction_rejects_self_consistent_but_false_projection(tmp_path, defect, message):
    _, receipt, path, binding, _ = _native_answer_revision_fixture(tmp_path)
    current = json.loads((tmp_path/'results.jsonl').read_text())
    proof = receipt['native_answer_corrections'][0]
    if defect == 'clock': current['elapsed_ms'] += 1
    elif defect == 'metadata': current['provider_metadata']['injected'] = 'new answer data'
    elif defect == 'answer':
        artifact = tmp_path/current['artifact_path']
        artifact.write_text('Different answer not supplied by the call')
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        current['metrics']['content_sha256'] = digest
        proof['corrected_artifact']['sha256'] = digest
    elif defect == 'source':
        source = tmp_path/proof['native_response']['path']
        source.write_text(json.dumps({'content': 'Different native response'}))
        proof['native_response']['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    else: receipt.pop('native_answer_corrections')
    (tmp_path/'results.jsonl').write_text(json.dumps(current)+'\n')
    receipt['output_results_sha256'] = hashlib.sha256((tmp_path/'results.jsonl').read_bytes()).hexdigest()
    path.write_text(json.dumps(receipt))
    binding['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=message):
        require_local_revision(tmp_path, binding, json.loads((tmp_path/'dataset.json').read_text()), [current])


@pytest.mark.parametrize('field,changed', [('title', 'forged title'), ('final_url', 'https://forged.example.test/')])
def test_native_context_revision_replays_native_fields_and_rejects_tampering(tmp_path, field, changed):
    original, _, _, _, _ = _native_answer_revision_fixture(tmp_path)
    current = json.loads((tmp_path/'results.jsonl').read_text())
    native_path = tmp_path/current['provider_metadata']['raw_response_path']
    # Prepare a new immutable native acquisition fixture for this transition.
    payload = json.loads(native_path.read_bytes())
    payload.update(title='native title', final_url='https://returned.example.test/')
    native = json.dumps(payload).encode()
    second_native = tmp_path/'private_artifacts/context.response.bin'
    second_native.write_bytes(native)
    current['provider_metadata'].update(raw_response_path='private_artifacts/context.response.bin',
        raw_response_sha256=hashlib.sha256(native).hexdigest(), raw_response_bytes=len(native))
    current.update(title=None, final_url=current['url'])
    (tmp_path/'results.jsonl').write_text(json.dumps(current)+'\n')
    config = {'cases': [], 'authority': {'description': 'Recover native title and redirect without new calls.'},
        'regression_ids': ['native-title-must-match-original', 'native-returned-title-remains-answer',
                           'native-final-url-must-match-original', 'native-returned-redirect-resolves-relative-image'],
        'native_answer_corrections': [{'attempt_id': current['attempt_id'],
            'native_response': {'path': 'private_artifacts/context.response.bin', 'sha256': hashlib.sha256(native).hexdigest()},
            'projection': {'schema_version': 2, 'content_format': 'text', 'content_pointer': ['content'],
                'result_fields': {key: {'pointer': [key]} for key in ('title', 'final_url')}}}]}
    config_path = tmp_path/'context-correction.json'
    config_path.write_text(json.dumps(config))
    receipt = revise_local_snapshot(tmp_path, config_path)
    fixed = json.loads((tmp_path/'results.jsonl').read_text())
    assert fixed['title'] == 'native title'
    assert fixed['final_url'] == 'https://returned.example.test/'
    assert fixed['elapsed_ms'] == original['elapsed_ms']
    assert receipt['provider_calls_added'] == 0
    from harness.web_fetch.local_revision import _require_native_answer_correction
    proof = receipt['native_answer_corrections'][0]
    _require_native_answer_correction(tmp_path, current, fixed, proof)
    from harness.web_fetch.local_revision import compact_local_snapshot
    history = next(path for path in (tmp_path/'local-evidence/offline-revisions').glob('*/receipt.json')
                   if json.loads(path.read_text()) == receipt)
    binding = {'path': history.relative_to(tmp_path).as_posix(),
               'sha256': hashlib.sha256(history.read_bytes()).hexdigest()}
    compact = compact_local_snapshot(tmp_path, binding)
    rows = [json.loads(line) for line in (tmp_path/'results.jsonl').read_text().splitlines()]
    require_local_revision(tmp_path, compact, json.loads((tmp_path/'dataset.json').read_text()), rows)
    fixed[field] = changed
    with pytest.raises(ValueError, match='context correction'):
        _require_native_answer_correction(tmp_path, current, fixed, proof)


def _context_fixture(tmp_path):
    raw = b'Page body retained exactly'
    url = 'https://example.test/'
    diagnostic = f'DEBUG fetch request complete method=GET url={url} final_url={url} status=418\n'
    def put(name, content):
        data = content if isinstance(content, bytes) else content.encode()
        (tmp_path / name).write_bytes(data)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}
    stdout = put('answer.txt', raw)
    diag = put('diagnostic.txt', diagnostic)
    case = {'id': 'a', 'url': url, 'required_facts': [
        {'id': 'body', 'alternatives': ['Page body retained exactly']}]}
    call = {'identity': {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a'},
        'request': {'url': url, 'route': 'ordinary_markdown'},
        'output': {**stdout, 'bytes': len(raw)},
        'process': {'exit_code': 0, 'stderr': diagnostic,
            'stdout_truncated': False, 'stderr_truncated': False},
        'capture': {'process_stdout': 'bound_as_output', 'process_stderr': 'captured_complete'}}
    binding = put('call.json', json.dumps(call))
    row = {'attempt_id': 'p__a', 'provider': 'p', 'provider_type': 'moli_webfetch',
        'case_id': 'a', 'url': url, 'ok': True, 'status_code': None,
        'artifact_path': 'answer.txt', 'elapsed_ms': 17, 'started_at': 'preserved',
        'provider_metadata': {'call_log': binding}, 'metrics': score_attempt(case, raw, ok=True)}
    (tmp_path / 'dataset.json').write_text(json.dumps({'cases': [case], 'comparison': {}}))
    (tmp_path / 'results.jsonl').write_text(json.dumps(row) + '\n')
    request = {'attempt_id': 'p__a', 'stdout': stdout, 'diagnostic': diag,
        'call_log': binding, 'projection': {'schema_version': 1, 'format': 'fetch_log_http_error'}}
    config = {'cases': [], 'authority': {'description': 'Same physical call context correction'},
        'regression_ids': ['explicit-body-request-failure-stays-failed'],
        'native_context_corrections': [request]}
    return raw, row, request, config, put


def test_context_only_revision_replays_same_call_and_preserves_answer(tmp_path):
    raw, old, request, config, put = _context_fixture(tmp_path)
    configuration = tmp_path / 'config.json'
    configuration.write_text(json.dumps(config))
    receipt = revise_local_snapshot(tmp_path, configuration)
    new = json.loads((tmp_path / 'results.jsonl').read_text())
    assert (new['ok'], new['status_code'], new['error_type'], new['error_message']) == (
        False, 418, 'api_error', 'Moli returned HTTP 418')
    assert (tmp_path / 'answer.txt').read_bytes() == raw
    assert new['artifact_path'] == old['artifact_path']
    assert new['provider_metadata'] == old['provider_metadata']
    assert new['started_at'] == old['started_at'] and new['elapsed_ms'] == 17
    assert new['metrics']['content_sha256'] == old['metrics']['content_sha256']
    assert receipt['native_context_corrections'][0]['original_context']['ok'] is True
    assert receipt['native_context_corrections'][0]['diagnostic_source'] == 'bound_physical_call_process_stderr'
    assert receipt['provider_calls_added'] == 0


@pytest.mark.parametrize('mutation', ['diagnostic', 'identity', 'output', 'incomplete', 'nonzero', 'ambiguity', 'fields'])
def test_context_replay_rejects_unbound_or_ambiguous_context(tmp_path, mutation):
    from harness.web_fetch.local_revision import _native_context_proof
    raw, row, request, config, put = _context_fixture(tmp_path)
    call = json.loads((tmp_path / 'call.json').read_text())
    if mutation == 'diagnostic':
        request['diagnostic'] = put('other.txt', 'invented diagnostic')
    elif mutation == 'identity':
        call['identity']['attempt_id'] = 'other'
    elif mutation == 'output':
        call['output']['sha256'] = '0' * 64
    elif mutation == 'incomplete':
        call['process']['stderr_truncated'] = True
    elif mutation == 'nonzero':
        call['process']['exit_code'] = 1
    elif mutation == 'ambiguity':
        call['process']['stderr'] += 'DEBUG detached committed main WindowProxy for replacement context page_id=1\n'
        request['diagnostic'] = put('diagnostic.txt', call['process']['stderr'])
    elif mutation == 'fields':
        request['projection']['ok'] = False
    binding = put('call.json', json.dumps(call))
    row['provider_metadata']['call_log'] = binding
    request['call_log'] = binding
    with pytest.raises(ValueError):
        _native_context_proof(tmp_path, row, request)


def test_context_revision_validator_rejects_answer_and_time_changes(tmp_path):
    from harness.web_fetch.local_revision import _native_context_proof, _require_native_context_correction
    raw, row, request, config, put = _context_fixture(tmp_path)
    proof, fields = _native_context_proof(tmp_path, row, request)
    corrected = {**row, **fields}
    _require_native_context_correction(tmp_path, row, corrected, proof)
    for changes in ({'elapsed_ms': 18}, {'artifact_path': 'different.txt'}, {'provider_metadata': {}}):
        with pytest.raises(ValueError, match='physical call facts or answer'):
            _require_native_context_correction(tmp_path, row, {**corrected, **changes}, proof)


@pytest.mark.parametrize('status', [200, 302, 600])
def test_context_projection_only_accepts_captured_http_errors(status):
    from harness.web_fetch.providers.moli import replay_native_http_error_context
    url = 'https://example.test/'
    diagnostic = f'DEBUG fetch request complete method=GET url={url} final_url={url} status={status}\n'.encode()
    with pytest.raises(ValueError, match='no unambiguous captured HTTP error'):
        replay_native_http_error_context(b'unchanged answer', diagnostic, url, 0,
            {'schema_version': 1, 'format': 'fetch_log_http_error'})


@pytest.mark.parametrize('defect', [None, 'wrong_public_pair', 'missing_public_pair'])
def test_native_public_projection_uses_bound_answer_view_while_private_bytes_restored(tmp_path, defect):
    from harness.web_fetch.local_revision import _require_native_answer_correction
    from harness.web_fetch.publication_privacy import project_evidence_view
    original, receipt, _, _, _ = _native_answer_revision_fixture(tmp_path)
    corrected = json.loads((tmp_path/'results.jsonl').read_text())
    proof = receipt['native_answer_corrections'][0]
    (tmp_path/'artifacts').mkdir()
    # Public views may only stand for answer/native evidence roots.
    for row, key in [(original, 'original_artifact'), (corrected, 'corrected_artifact')]:
        old = tmp_path/row['artifact_path']
        new = tmp_path/'artifacts'/old.name
        old.rename(new)
        row['artifact_path'] = str(new.relative_to(tmp_path))
        proof[key]['path'] = row['artifact_path']
    private_after = (tmp_path/corrected['artifact_path']).read_bytes()
    entries = []
    def write_binding(name, data):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        return {'path':name,'sha256':hashlib.sha256(data).hexdigest()}
    for binding, role in [(proof['native_response'], 'native_response'), (proof['corrected_artifact'], 'scored_answer')]:
        raw=(tmp_path/binding['path']).read_bytes()
        name='public-evidence/'+role+('.json' if role=='native_response' else '.txt')
        public, view=project_evidence_view(name,raw,role=role,host_prefixes=['/Users/person'])
        if role=='scored_answer' and defect=='missing_public_pair':
            continue
        if role=='scored_answer' and defect=='wrong_public_pair':
            public=b'Different public content';view.update(published_sha256=hashlib.sha256(public).hexdigest(),published_bytes=len(public))
        write_binding(name,public)
        view_binding=write_binding(name+'.receipt.json',json.dumps(view).encode())
        entries.append({'original_path':binding['path'],'original_sha256':binding['sha256'],'view_receipt':view_binding})
    index=write_binding('public-evidence/index.json',json.dumps({'kind':'post_score_public_evidence_view_inventory','provider_calls_added':0,'files':entries}).encode())
    (tmp_path/'presentation-binding.json').write_text(json.dumps({'publication_evidence_views':index}))
    (tmp_path/proof['native_response']['path']).unlink()
    if defect:
        with pytest.raises(ValueError,match='bound public answer pair|declared original fields'):
            _require_native_answer_correction(tmp_path,original,corrected,proof)
    else:
        _require_native_answer_correction(tmp_path,original,corrected,proof)
    assert (tmp_path/corrected['artifact_path']).read_bytes()==private_after
    assert hashlib.sha256(private_after).hexdigest()==proof['corrected_artifact']['sha256']


@pytest.mark.parametrize('defect', ['missing_predicate', 'missing_manifest', 'wrong_predicate_hash'])
def test_bad_frozen_input_proof_rejects_before_scoring_or_writes(tmp_path, monkeypatch, defect):
    _, _, _, _, _ = _native_answer_revision_fixture(tmp_path)
    config = json.loads((tmp_path/'correction.json').read_text())
    results = (tmp_path/'results.jsonl').read_bytes()
    def put(name, data):
        (tmp_path/name).write_bytes(data)
        return {'path':name,'sha256':hashlib.sha256(data).hexdigest()}
    proof = {'input_results_sha256':hashlib.sha256(results).hexdigest(),
        'verdicts':{'p__a':True},'frozen_predicate':put('predicate.py',b'def task_passed(ok, metrics): return ok\n'),
        'snapshot_manifest':put('snapshot.json',b'{}')}
    if defect=='missing_predicate':proof.pop('frozen_predicate')
    elif defect=='missing_manifest':proof.pop('snapshot_manifest')
    else:proof['frozen_predicate']['sha256']='0'*64
    config['input_verdicts']=put('input-verdicts.json',json.dumps(proof).encode())
    configuration=tmp_path/'next.json';configuration.write_text(json.dumps(config))
    before={p.relative_to(tmp_path):p.read_bytes()for p in tmp_path.rglob('*')if p.is_file()}
    def forbidden_score(*args,**kwargs):raise AssertionError('Attempt scoring started before proof preflight')
    monkeypatch.setattr('lexbench_webfetch.scoring.score_attempt',forbidden_score)
    with pytest.raises(ValueError,match='Frozen input verdict proof|binding changed'):
        revise_local_snapshot(tmp_path,configuration)
    assert before=={p.relative_to(tmp_path):p.read_bytes()for p in tmp_path.rglob('*')if p.is_file()}


def test_bad_predecessor_rejects_before_scoring_or_writes(tmp_path, monkeypatch):
    _, (latest_binding, _) = _two_revision_chain(tmp_path)
    config={'cases':[],'rescore_attempt_ids':['p__a'],
        'authority':{'description':'Declared next revision'},
        'regression_ids':['ordinary-text-player-control-only','ordinary-text-with-player-control'],
        'predecessor_revision':{**latest_binding,'sha256':'0'*64}}
    configuration=tmp_path/'next.json';configuration.write_text(json.dumps(config))
    before={p.relative_to(tmp_path):p.read_bytes()for p in tmp_path.rglob('*')if p.is_file()}
    def forbidden_score(*args,**kwargs):raise AssertionError('Attempt scoring started before predecessor preflight')
    monkeypatch.setattr('lexbench_webfetch.scoring.score_attempt',forbidden_score)
    with pytest.raises(ValueError,match='binding changed'):
        revise_local_snapshot(tmp_path,configuration)
    assert before=={p.relative_to(tmp_path):p.read_bytes()for p in tmp_path.rglob('*')if p.is_file()}
