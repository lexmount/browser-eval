import hashlib
import json

import pytest

from harness.web_fetch.local_revision import require_local_revision, revise_local_snapshot
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.metrics import task_passed


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
    fixed[field] = changed
    with pytest.raises(ValueError, match='context correction'):
        _require_native_answer_correction(tmp_path, current, fixed, proof)
