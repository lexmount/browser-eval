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


def test_policy_revision_records_version_and_rejects_parser_only_label(tmp_path):
    raw = b'Required body\nCAPTCHA'
    (tmp_path / 'answer.txt').write_bytes(raw)
    case = {'id': 'a', 'url': 'https://example.test/', 'task_kind': 'body', 'acceptance_policy_version': 1,
            'forbidden_any': ['CAPTCHA'], 'acceptance_contract': {'version': 1, 'schema': 'body_text',
                'minimum_records': 0, 'stable_text_anchors': [{'id': 'body', 'alternatives': ['Required body']}]}}
    original = {'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'ok': True,
                'artifact_path': 'answer.txt', 'metrics': score_attempt(case, raw, ok=True)}
    assert not task_passed(True, original['metrics'])
    (tmp_path / 'dataset.json').write_text(json.dumps({'cases': [case], 'comparison': {}}))
    (tmp_path / 'results.jsonl').write_text(json.dumps(original)+'\n')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'cases': [{**case, 'acceptance_policy_version': 2}], 'rule_revision': True,
        'authority': {'description': 'Explicitly adopt policy 2; verification words are diagnostics'},
        'regression_ids': ['html-doctype-visible-challenge-policy-v2-diagnostic', 'prompt-diagnostic-only-verification']}))
    receipt = revise_local_snapshot(tmp_path, config)
    assert receipt['rule_revision'] is True and receipt['acceptance_policy_version'] == 2
    rows = [json.loads((tmp_path / 'results.jsonl').read_text())]
    assert task_passed(True, rows[0]['metrics'])
    rows[0]['scoring']['rule_revision'] = False
    (tmp_path / 'results.jsonl').write_text(json.dumps(rows[0])+'\n')
    path = next((tmp_path / 'local-evidence/offline-revisions').glob('*/receipt.json'))
    receipt['output_results_sha256'] = hashlib.sha256((tmp_path / 'results.jsonl').read_bytes()).hexdigest()
    path.write_text(json.dumps(receipt))
    binding = {'path': str(path.relative_to(tmp_path)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='parser-only'):
        require_local_revision(tmp_path, binding, json.loads((tmp_path / 'dataset.json').read_text()), rows)
