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


@pytest.mark.parametrize('broken', [False, True])
def test_score_revision_chain_reaches_the_original_replacement_without_rewriting_it(tmp_path, broken):
    from harness.web_fetch.local_revision import replacement_predecessor
    def save(name, document):
        path = tmp_path/name
        path.write_text(json.dumps(document))
        return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    first_dataset = save('first-dataset.json', {'cases': []})
    first_results = save('first-results.json', [])
    middle_dataset = save('middle-dataset.json', {'cases': [], 'stage': 'middle'})
    middle_results = save('middle-results.json', ['middle'])
    parent = save('parent.json', {'kind': 'local_offline_score_revision', 'provider_calls_added': 0,
        'input_dataset': first_dataset, 'input_results': first_results,
        'output_dataset_sha256': middle_dataset['sha256'],
        'output_results_sha256': 'x'*64 if broken else middle_results['sha256']})
    latest = save('latest.json', {'kind': 'local_offline_score_revision', 'provider_calls_added': 0,
        'input_dataset': middle_dataset, 'input_results': middle_results,
        'predecessor_revision': parent})
    if broken:
        with pytest.raises(ValueError, match='predecessor does not match'):
            replacement_predecessor(tmp_path, latest)
    else:
        assert replacement_predecessor(tmp_path, latest) == (first_dataset, first_results)
