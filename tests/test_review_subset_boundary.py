"""Supplemental review preparation must preserve acquisition evidence and fail atomically."""
import hashlib
import json
from unittest.mock import patch

import pytest

from harness.web_fetch.artifacts import materialize_bound_artifacts
from harness.web_fetch.result_review import prepare_review_subset


def evidence_fixture(tmp_path):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'artifacts').mkdir()
    rows = []
    for case in ('first', 'second'):
        for provider in ('a', 'b'):
            payload = f'{provider}: {case}'.encode()
            relative = f'artifacts/{provider}-{case}.txt'
            (evidence / relative).write_bytes(payload)
            rows.append({'attempt_id': f'{provider}-{case}', 'case_id': case,
                         'provider': provider, 'ok': True, 'artifact_path': relative,
                         'metrics': {'content_sha256': hashlib.sha256(payload).hexdigest(),
                                     'content_pass': True}})
    (evidence / 'dataset.json').write_text(json.dumps({'cases': [{'id': 'first'}, {'id': 'second'}]}))
    (evidence / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return evidence, rows


def test_complete_case_subset_preserves_bound_bytes_and_only_selected_attempts(tmp_path):
    evidence, rows = evidence_fixture(tmp_path)
    output = tmp_path / 'subset'
    counts = prepare_review_subset(evidence, output, case_ids=['second'])
    assert counts == {'cases': 1, 'attempts': 2}
    assert json.loads((output / 'dataset.json').read_text())['cases'] == [{'id': 'second'}]
    selected = [row for row in rows if row['case_id'] == 'second']
    assert [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()] == selected
    assert sorted(path.name for path in (output / 'artifacts').iterdir()) == ['a-second.txt', 'b-second.txt']
    for row in selected:
        assert (output / row['artifact_path']).read_bytes() == (evidence / row['artifact_path']).read_bytes()


@pytest.mark.parametrize('fault', ['missing', 'wrong-hash', 'symlink', 'incomplete', 'unknown'])
def test_invalid_scope_or_artifact_leaves_no_partial_output(tmp_path, fault):
    evidence, rows = evidence_fixture(tmp_path)
    selected = evidence / rows[2]['artifact_path']
    if fault == 'missing':
        selected.unlink()
    elif fault == 'wrong-hash':
        selected.write_text('corrupt')
    elif fault == 'symlink':
        selected.unlink()
        selected.symlink_to(evidence / rows[0]['artifact_path'])
    elif fault == 'incomplete':
        (evidence / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows[:-1]))
    output = tmp_path / 'subset'
    with pytest.raises((ValueError, FileNotFoundError)):
        prepare_review_subset(evidence, output, case_ids=['absent' if fault == 'unknown' else 'second'])
    assert not output.exists()
    assert not list(tmp_path.glob('.subset-revision-*'))


def test_source_mutation_during_copy_does_not_expose_subset(tmp_path):
    evidence, _ = evidence_fixture(tmp_path)
    output = tmp_path / 'subset'

    def mutate_after_copy(destination, source, records):
        materialize_bound_artifacts(destination, source, records)
        (source / 'dataset.json').write_text('{}')

    with patch('harness.web_fetch.artifacts.materialize_bound_artifacts', side_effect=mutate_after_copy):
        with pytest.raises(ValueError, match='Source evidence changed'):
            prepare_review_subset(evidence, output, case_ids=['second'])
    assert not output.exists()


@pytest.mark.parametrize('destination', ['existing', 'nested', 'linked'])
def test_unsafe_destination_is_rejected_without_changes(tmp_path, destination):
    evidence, _ = evidence_fixture(tmp_path)
    before = {path.relative_to(evidence): path.read_bytes() for path in evidence.rglob('*') if path.is_file()}
    output = tmp_path / 'subset'
    if destination == 'existing':
        output.mkdir()
        (output / 'keep').write_text('preserve')
    elif destination == 'nested':
        output = evidence / 'subset'
    else:
        output.symlink_to(tmp_path / 'absent', target_is_directory=True)
    with pytest.raises(ValueError):
        prepare_review_subset(evidence, output, case_ids=['second'])
    assert {path.relative_to(evidence): path.read_bytes() for path in evidence.rglob('*') if path.is_file()} == before
    if destination == 'existing':
        assert (output / 'keep').read_text() == 'preserve'
