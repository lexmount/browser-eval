"""Public-copy rescoring must disclose changed bytes and preserve call facts."""
import hashlib
import json

import pytest

from harness.web_fetch.rescoring import score_published_snapshot
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def snapshot(tmp_path):
    case = {'id': 'one', 'name': 'One', 'url': 'https://example.test/',
            'category': 'web', 'track': 'web', 'required_facts': [
                {'id': 'fact', 'alternatives': ['complete answer']}]}
    raw = b'complete answer'
    public = b'[REDACTED]'
    original = tmp_path / 'original.txt'
    original.write_bytes(raw)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'answer.txt').write_bytes(public)
    dataset = {'cases': [case], 'coverage_plan': {'web': ['sample']}}
    record = {'attempt_id': 'sample__one__r001', 'provider': 'sample',
              'case_id': 'one', 'case_name': 'One', 'category': 'web',
              'url': case['url'], 'run': 1, 'ok': True, 'elapsed_ms': 43.5,
              'run_id': 'unchanged-call', 'artifact_path': 'answer.txt',
              'provider_metadata': {}, 'metrics': score_attempt(case, raw, ok=True)}
    (source / 'dataset.json').write_text(json.dumps(dataset))
    (source / 'results.jsonl').write_text(json.dumps(record) + '\n')
    receipt = {'files': [{'path': 'answer.txt', 'scored_output': True,
        'original_sha256': hashlib.sha256(raw).hexdigest(),
        'published_sha256': hashlib.sha256(public).hexdigest(),
        'published_bytes': len(public)}]}
    (source / 'publication-privacy.json').write_text(json.dumps(receipt))
    return source, dataset, record, original


@pytest.mark.parametrize('recover', [False, True])
def test_exact_public_or_recovered_input_is_explicit(tmp_path, recover):
    source, dataset, old, original = snapshot(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    rows, receipt = score_published_snapshot(source, dataset,
        recovered={'answer.txt': original} if recover else None)
    row = rows[0]
    assert task_passed(row['ok'], row['metrics']) is recover
    assert row['scoring']['artifact_view'] == ('recovered_original' if recover else 'published_projection')
    assert receipt['projected_scoring_inputs'] == (0 if recover else 1)
    assert receipt['provider_calls_added'] == 0
    assert {k: v for k, v in old.items() if k != 'metrics'} == {
        k: v for k, v in row.items() if k not in {'metrics', 'scoring'}}
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before


@pytest.mark.parametrize('defect', ['public_hash', 'original_hash', 'size', 'recovered_hash', 'matrix', 'input'])
def test_unbound_or_changed_evidence_cannot_be_scored(tmp_path, defect):
    source, dataset, _, original = snapshot(tmp_path)
    recovered = None
    receipt_path = source / 'publication-privacy.json'
    receipt = json.loads(receipt_path.read_text())
    if defect in {'public_hash', 'original_hash'}:
        receipt['files'][0]['published_sha256' if defect == 'public_hash' else 'original_sha256'] = '0' * 64
    elif defect == 'size':
        receipt['files'][0]['published_bytes'] += 1
    elif defect == 'recovered_hash':
        original.write_bytes(b'other answer')
        recovered = {'answer.txt': original}
    elif defect == 'matrix':
        dataset['cases'] = []
    else:
        dataset['cases'][0]['url'] = 'https://example.test/other'
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        score_published_snapshot(source, dataset, recovered=recovered)
