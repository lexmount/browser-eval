"""Scoring defaults may not replace the input versions recorded by a collected run."""
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from harness.web_fetch import webmainbench_scoring as scoring


@pytest.fixture
def command(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    dataset = tmp_path / 'selected.jsonl'
    records = [{'track_id': 'same-case' if i == 0 else f'case-{i}',
                'groundtruth_content': 'new reference'} for i in range(545)]
    dataset.write_text(''.join(json.dumps(row) + '\n' for row in records))
    old_dataset = tmp_path / 'historical.jsonl'
    old_dataset.write_text(''.join(json.dumps({**row, 'groundtruth_content': 'historical reference'})
                                   + '\n' for row in records))
    old_sha = hashlib.sha256(old_dataset.read_bytes()).hexdigest()
    selected_sha = hashlib.sha256(dataset.read_bytes()).hexdigest()
    scorer = tmp_path / 'scorer'
    scorer.mkdir()
    current_commit = 'b' * 40
    artifact = evidence / 'answer.txt'
    artifact.write_text('saved answer')
    (evidence / 'results.jsonl').write_text(json.dumps({'track_id': 'same-case', 'provider': 'fixture',
        'ok': True, 'artifact_path': artifact.name, 'output_sha256': scoring.sha256_path(artifact), 'meta': {}}) + '\n')
    def select(path, expected, commit):
        return path or dataset, expected or selected_sha, commit or current_commit
    monkeypatch.setattr(scoring, 'selected_input', select)
    from harness.benchmarks import data
    monkeypatch.setattr(data, 'binding', lambda track: {'scorer': {'revision': current_commit}})
    calls = []
    monkeypatch.setattr(scoring, 'prepare_scorer', lambda: calls.append('prepare') or scorer)
    original_loader = scoring.load_records
    def load(path, expected):
        calls.append(('load', path, expected))
        return original_loader(path, expected)
    monkeypatch.setattr(scoring, 'load_records', load)
    metric = SimpleNamespace(score=1.0, success=True, details={})
    def calculator(repo, cache, commit):
        calls.append(('scorer', commit))
        def calculate(**kwargs):
            calls.append(('reference', kwargs['groundtruth_content']))
            return {'text_edit': metric}
        return SimpleNamespace(calculate_all=calculate)
    monkeypatch.setattr(scoring, 'official_calculator', calculator)
    def run(recorded_sha=selected_sha, recorded_commit=current_commit, extra=(), manifest=True):
        if manifest:
            (evidence / 'dataset.json').write_text(json.dumps({'source_sha256': recorded_sha,
                'official_scorer_commit': recorded_commit, 'case_ids': ['same-case']}))
        monkeypatch.setattr(sys, 'argv', ['score', '--evidence', str(evidence), *extra])
        return scoring.main()
    return SimpleNamespace(run=run, evidence=evidence, dataset=dataset, sha=selected_sha,
        scorer=scorer, commit=current_commit, calls=calls, old_dataset=old_dataset, old_sha=old_sha)


@pytest.mark.parametrize('mismatch', ['dataset', 'scorer', 'both', 'missing-manifest'])
def test_historical_or_unbound_evidence_rejects_silent_current_defaults(command, mismatch):
    with pytest.raises((SystemExit, ValueError), match=None):
        command.run(recorded_sha='a' * 64 if mismatch in ('dataset', 'both') else command.sha,
                    recorded_commit='a' * 40 if mismatch in ('scorer', 'both') else command.commit,
                    manifest=mismatch != 'missing-manifest')
    assert command.calls == []
    assert not (command.evidence / 'scored-results.jsonl').exists()
    assert not (command.evidence / 'score-summary.json').exists()


def test_current_evidence_keeps_matching_default_convenience(command):
    command.run()
    assert command.calls[0] == 'prepare'
    assert ('reference', 'new reference') in command.calls
    assert (command.evidence / 'scored-results.jsonl').is_file()
    assert json.loads((command.evidence / 'score-summary.json').read_bytes())['scorer']['commit'] == command.commit


def test_explicit_historical_inputs_remain_supported(command):
    old_sha, old_commit = command.old_sha, 'a' * 40
    command.run(old_sha, old_commit, ['--dataset', str(command.old_dataset),
        '--expected-dataset-sha256', old_sha, '--scorer-repo', str(command.scorer),
        '--expected-scorer-commit', old_commit])
    assert 'prepare' not in command.calls
    assert ('scorer', old_commit) in command.calls
    assert ('reference', 'historical reference') in command.calls


def test_explicit_versioned_correction_can_change_the_original_bindings(command):
    command.run('a' * 64, 'a' * 40, ['--dataset', str(command.dataset),
        '--expected-dataset-sha256', command.sha, '--scorer-repo', str(command.scorer),
        '--expected-scorer-commit', command.commit, '--revision', 'reference-fix'])
    assert ('reference', 'new reference') in command.calls
    assert (command.evidence / 'scored-results-reference-fix.jsonl').is_file()
    assert json.loads((command.evidence / 'score-summary-reference-fix.json').read_bytes())['supersedes']
