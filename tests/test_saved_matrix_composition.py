"""Preserve prior calls while adding a method to the same frozen population."""
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix
from tests.test_saved_matrix_evidence import build, fixture, write


def reports(tmp_path, *, different_task=False, duplicate=False):
    entries = []
    for i in range(2):
        folder = tmp_path / str(i)
        spec, source, record, _ = fixture(folder)
        provider = 'method' if duplicate else 'method-' + str(i)
        spec['providers'] = [{'id': provider, 'label': provider}]
        record.update(provider=provider, attempt_id='run-' + str(i))
        raw = (json.dumps(record) + '\n').encode()
        (source / 'results.jsonl').write_bytes(raw)
        spec['sources'][0]['results']['sha256'] = hashlib.sha256(raw).hexdigest()
        if i and different_task:
            data = json.loads((source / 'dataset.json').read_bytes())
            data['cases'][0]['expected_any'] = ['different']
            spec['suites'][0]['dataset'] = write(source / 'dataset.json', data)
        root = build(folder, spec).parent
        audit = root / 'saved-matrix-audit.json'
        entries.append({'id': 'source-' + str(i), 'root': str(root),
                        'audit': {'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}})
    spec = {'schema_version': 1, 'title': 'Combined', 'saved_reports': entries}
    path = tmp_path / 'combined.json'
    write(path, spec)
    return path, entries


def test_composition_retains_exact_source_records(tmp_path):
    spec, entries = reports(tmp_path)
    output = build_saved_matrix(spec, tmp_path / 'combined').parent
    assert check_saved_matrix(output, spec_path=spec)['valid']
    model = json.loads((output / 'matrix.json').read_bytes())
    assert len(model['providers']) == 2 and len(model['rows']) == 2
    for row, entry in zip(model['rows'], entries, strict=True):
        source = Path(entry['root'])
        original = json.loads((source / 'matrix.json').read_bytes())['rows'][0]
        assert (output / row['record_path']).read_bytes() == (source / original['record_path']).read_bytes()
        assert row['passed'] == original['passed'] and row['elapsed_ms'] == original['elapsed_ms']
    (output / model['rows'][0]['answer_path']).write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError):
        check_saved_matrix(output)


def test_composition_packages_through_shared_owner(tmp_path):
    from harness.web_fetch.report_package import build as package
    spec, _ = reports(tmp_path)
    output = build_saved_matrix(spec, tmp_path / 'combined').parent
    package(output, tmp_path / 'combined.zip')


def test_composition_rejects_rebound_row_change(tmp_path):
    spec, _ = reports(tmp_path)
    output = build_saved_matrix(spec, tmp_path / 'combined').parent
    matrix = json.loads((output / 'matrix.json').read_bytes())
    matrix['rows'][0]['elapsed_ms'] = 0
    binding = write(output / 'matrix.json', matrix)
    audit = json.loads((output / 'saved-matrix-audit.json').read_bytes())
    audit['files']['matrix.json'] = binding['sha256']
    write(output / 'saved-matrix-audit.json', audit)
    with pytest.raises(ValueError, match='immutable source reports'):
        check_saved_matrix(output)


@pytest.mark.parametrize('option', ['different_task', 'duplicate'])
def test_composition_rejects_incompatible_reports(tmp_path, option):
    spec, _ = reports(tmp_path, **{option: True})
    with pytest.raises(ValueError, match='different frozen tasks|duplicate methods'):
        build_saved_matrix(spec, tmp_path / 'combined')


def test_composition_selects_method_without_changing_saved_results(tmp_path):
    spec, _ = reports(tmp_path)
    source = build_saved_matrix(spec, tmp_path / 'combined').parent
    audit = source / 'saved-matrix-audit.json'
    selection = {'schema_version': 1, 'title': 'Selected method', 'saved_reports': [{
        'id': 'prior', 'root': str(source), 'providers': ['method-1'],
        'audit': {'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}}]}
    path = tmp_path / 'selection.json'
    write(path, selection)
    output = build_saved_matrix(path, tmp_path / 'selected').parent
    assert check_saved_matrix(output, spec_path=path)['valid']
    model = json.loads((output / 'matrix.json').read_bytes())
    assert [p['id'] for p in model['providers']] == ['method-1']
    assert len(model['rows']) == 1
    row = model['rows'][0]
    original = json.loads((source / 'matrix.json').read_bytes())['rows'][1]
    assert (output / row['record_path']).read_bytes() == (source / original['record_path']).read_bytes()
    assert row['passed'] == original['passed'] and row['elapsed_ms'] == original['elapsed_ms']


@pytest.mark.parametrize('selected', [[], ['unknown'], ['method-0', 'method-0'], 'method-0', [None]])
def test_composition_rejects_invalid_method_selection(tmp_path, selected):
    path, _ = reports(tmp_path)
    spec = json.loads(path.read_bytes())
    spec['saved_reports'][0]['providers'] = selected
    write(path, spec)
    with pytest.raises(ValueError, match='Invalid saved report provider selection'):
        build_saved_matrix(path, tmp_path / 'combined')


def test_composition_preserves_shared_revision_attachments(tmp_path):
    from tests.test_saved_matrix_revision import revision_fixture
    spec, _, _ = revision_fixture(tmp_path, second_provider=True)
    spec['offline_revision']['review_files'] = {'notes': write(tmp_path / 'notes.json', {'reviewed': True})}
    path = tmp_path / 'revised-spec.json'
    write(path, spec)
    source = build_saved_matrix(path, tmp_path / 'revised').parent
    audit = source / 'saved-matrix-audit.json'
    combined = {'schema_version': 1, 'title': 'Shared attachments', 'saved_reports': [{
        'id': 'source', 'root': str(source),
        'audit': {'path': str(audit), 'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}}]}
    write(tmp_path / 'combined.json', combined)
    output = build_saved_matrix(tmp_path / 'combined.json', tmp_path / 'combined').parent
    assert check_saved_matrix(output)['valid']
    model = json.loads((output / 'matrix.json').read_bytes())
    assert len(model['rows']) == 2
    for row in model['rows']:
        receipt = json.loads((output / row['score_revision']['receipt_path']).read_bytes())
        attachment = output / receipt['review_files']['notes']
        assert json.loads(attachment.read_bytes()) == {'reviewed': True}
    attachment.write_text('{}')
    with pytest.raises(ValueError):
        check_saved_matrix(output)
