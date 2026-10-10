"""Neutral metadata updates preserve judgments and immutable evidence as one revision."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_fetch.reference_lifecycle import valid_reference_revision, valid_scoring_revision
from harness.web_fetch.result_review import rebind_metadata_review, require_result_review
from harness.web_fetch.safety import evidence_tree_identity


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, *, resource=False, references=True):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    case = {'id': 'one', 'task_kind': 'resource' if resource else 'body'}
    if resource:
        case.update(expected_resources=[{'id': 'hero', 'urls': ['/hero.jpg']}],
                    resource_output_contract={'unexpected': 'reject'}, task_prompt='Return hero image.')
    prior = {'name': 'Previous title', 'comparison': {'require_independent_result_review': True},
             'cases': [case]}
    prior_bytes = json.dumps(prior).encode()
    prior_path = tmp_path / 'prior-dataset.json'
    prior_path.write_bytes(prior_bytes)
    current = {**prior, 'name': 'Public title'}
    (evidence / 'dataset.json').write_text(json.dumps(current))
    (evidence / 'answer.txt').write_bytes(b'answer')
    row = {'attempt_id': 'custom-one', 'provider': 'custom', 'case_id': 'one', 'ok': True,
           'artifact_path': 'answer.txt', 'metrics': {'content_sha256': digest(b'answer'),
                                                    'resource_pass' if resource else 'content_pass': True}}
    results = (json.dumps(row) + '\n').encode()
    (evidence / 'results.jsonl').write_bytes(results)
    review = {'schema_version': 1, 'reviewer': {'model': 'synthetic'},
              'bindings': {'dataset_sha256': digest(prior_bytes), 'results_sha256': digest(results)},
              'review_protocol': {'scope': 'all', 'machine_judgments_withheld': True},
              'attempts': [{'attempt_id': 'custom-one', 'machine_verdict': 'pass',
                            'agent_verdict': 'pass', 'final_verdict': 'pass',
                            'agent_evidence': 'Synthetic acceptance control'}]}
    (evidence / 'result-review.json').write_text(json.dumps(review))
    receipt = {'kind': 'audited_scoring_rule_revision', 'provider_calls': 0,
               'input_dataset_sha256': 'a' * 64, 'input_results_sha256': 'b' * 64,
               'output_dataset_sha256': digest(prior_bytes), 'output_results_sha256': digest(results),
               'changed_cases': [], 'task_decision_changes': []}
    (evidence / 'scoring-revisions.json').write_text(json.dumps([receipt]))
    if references:
        reference = {**receipt, 'kind': 'audited_reference_correction',
                     'changed_cases': [{'case_id': 'one', 'fields': ['ground_truth_evidence'],
                                        'old_reference_sha256': 'c' * 64, 'new_reference_sha256': 'd' * 64}]}
        (evidence / 'reference-revisions.json').write_text(json.dumps([reference]))
    return evidence, prior_path, current, [row]


@pytest.mark.parametrize('resource,references', [(False, False), (False, True), (True, True)])
def test_metadata_rebind_preserves_originals_and_passes_release_validators(tmp_path, resource, references):
    evidence, prior, current, rows = fixture(tmp_path, resource=resource, references=references)
    original = evidence_tree_identity(evidence)
    history = json.loads((evidence / 'scoring-revisions.json').read_bytes())
    attempts = json.loads((evidence / 'result-review.json').read_bytes())['attempts']
    assert rebind_metadata_review(evidence, prior, rebound_at='2026-10-09T00:00:00+00:00') == evidence / 'result-review.json'
    assert valid_scoring_revision(evidence)
    if references:
        assert valid_reference_revision(evidence)
        assert digest((evidence / 'reference-revisions.json').read_bytes()) == original['reference-revisions.json']
    review = require_result_review(evidence, current, rows)
    assert review['attempts'] == attempts
    assert digest((evidence / 'results.jsonl').read_bytes()) == original['results.jsonl']
    assert digest((evidence / 'answer.txt').read_bytes()) == original['answer.txt']
    updated = json.loads((evidence / 'scoring-revisions.json').read_bytes())
    assert updated[:-1] == history
    assert updated[-1]['metadata_change'] == {'changed_top_level_fields': ['name']}
    predecessor, = tmp_path.glob('.evidence-metadata-history-*/original')
    assert evidence_tree_identity(predecessor) == original


@pytest.mark.parametrize('fault', ['case', 'policy', 'coverage', 'results', 'missing-history',
                                   'invalid-history', 'broken-chain', 'answer', 'link', 'pinned'])
def test_invalid_rebind_preserves_complete_source(tmp_path, fault):
    evidence, prior, current, rows = fixture(tmp_path)
    if fault in {'case', 'policy', 'coverage'}:
        if fault == 'case':
            current['cases'][0]['task_prompt'] = 'Different task'
        elif fault == 'policy':
            current['comparison']['require_anomaly_review'] = True
        else:
            current['coverage_plan'] = {'web': ['custom']}
        (evidence / 'dataset.json').write_text(json.dumps(current))
    elif fault == 'results':
        rows[0]['ok'] = False
        (evidence / 'results.jsonl').write_text(json.dumps(rows[0]) + '\n')
    elif fault == 'missing-history':
        (evidence / 'scoring-revisions.json').unlink()
    elif fault == 'invalid-history':
        (evidence / 'scoring-revisions.json').write_text('{}')
    elif fault == 'broken-chain':
        history = json.loads((evidence / 'scoring-revisions.json').read_bytes())
        history[0]['output_dataset_sha256'] = 'e' * 64
        (evidence / 'scoring-revisions.json').write_text(json.dumps(history))
    elif fault == 'answer':
        (evidence / 'answer.txt').write_bytes(b'corrupt')
    elif fault == 'link':
        external = tmp_path / 'external-history.json'
        path = evidence / 'scoring-revisions.json'
        external.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(external)
        external_bytes = external.read_bytes()
    else:
        (evidence / 'source-inputs.json').write_text('{}')
    before = {str(path.relative_to(evidence)): path.read_bytes() for path in evidence.rglob('*') if path.is_file()}
    with pytest.raises(ValueError):
        rebind_metadata_review(evidence, prior)
    assert {str(path.relative_to(evidence)): path.read_bytes() for path in evidence.rglob('*') if path.is_file()} == before
    assert not list(tmp_path.glob('.evidence-metadata-history-*'))
    if fault == 'link':
        assert (tmp_path / 'external-history.json').read_bytes() == external_bytes


def test_failed_directory_swap_restores_entire_predecessor(tmp_path):
    evidence, prior, _, _ = fixture(tmp_path)
    original = evidence_tree_identity(evidence)
    rename = Path.rename

    def fail_install(path, target):
        if path.name == 'staged' and Path(target) == evidence:
            raise OSError('synthetic installation failure')
        return rename(path, target)

    with patch.object(Path, 'rename', fail_install):
        with pytest.raises(OSError, match='installation failure'):
            rebind_metadata_review(evidence, prior)
    assert evidence_tree_identity(evidence) == original
    assert not list(tmp_path.glob('.evidence-metadata-history-*'))


def test_source_change_during_staging_is_preserved_not_rolled_back(tmp_path):
    evidence, prior, _, _ = fixture(tmp_path)
    original_review = (evidence / 'result-review.json').read_bytes()
    from harness.web_fetch.assembly import bind_assembled_scoring_revision

    def concurrent_update(staged):
        outcome = bind_assembled_scoring_revision(staged)
        (evidence / 'answer.txt').write_bytes(b'concurrent owner update')
        return outcome

    with patch('harness.web_fetch.assembly.bind_assembled_scoring_revision', side_effect=concurrent_update):
        with pytest.raises(ValueError, match='inputs changed'):
            rebind_metadata_review(evidence, prior)
    assert (evidence / 'answer.txt').read_bytes() == b'concurrent owner update'
    assert (evidence / 'result-review.json').read_bytes() == original_review


def test_repeated_neutral_changes_keep_reference_history_immutable(tmp_path):
    evidence, prior, current, rows = fixture(tmp_path)
    reference_bytes = (evidence / 'reference-revisions.json').read_bytes()
    rebind_metadata_review(evidence, prior)
    prior.write_bytes((evidence / 'dataset.json').read_bytes())
    current['name'] = 'Second public title'
    (evidence / 'dataset.json').write_text(json.dumps(current))
    rebind_metadata_review(evidence, prior)
    assert (evidence / 'reference-revisions.json').read_bytes() == reference_bytes
    assert valid_reference_revision(evidence) and valid_scoring_revision(evidence)
    assert require_result_review(evidence, current, rows)['reviewed_attempts'] == 1
    assert len(json.loads((evidence / 'scoring-revisions.json').read_bytes())) == 3
