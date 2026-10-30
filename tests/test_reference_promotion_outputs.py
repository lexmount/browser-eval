"""Candidate and receipt destinations must not overwrite one another."""
import pytest

from harness.web_fetch import reference_freeze
from harness.web_fetch.reference_freeze import prepare_promotion
from tests.reference_freeze_fixture import REGRESSIONS, ROOT_CAUSE, create_promotion_inputs


@pytest.mark.parametrize('receipt,candidate,canonical', [
    ('receipt.json', 'receipt.json', None),
    ('receipt.json', 'nested/../receipt.json', None),
    ('receipt.json', 'candidate.json', 'receipt.json'),
    ('receipt.json', 'candidate.json', 'candidate.json'),
    ('bundle', 'bundle/candidate.json', None),
    ('bundle/receipt.json', 'bundle', None),
])
def test_overlapping_destinations_reject_without_changing_any_inputs(tmp_path, receipt, candidate, canonical):
    inputs = create_promotion_inputs(tmp_path)
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob('*') if path.is_file()}
    with pytest.raises(ValueError, match='overlap|distinct'):
        prepare_promotion(dataset_path=inputs['dataset_path'], captures=inputs['captures'],
                          output=tmp_path / receipt, candidate_output=tmp_path / candidate,
                          full_canonical_output=tmp_path / canonical if canonical else None,
                          browser_observations=inputs['observations_path'],
                          approved_by='synthetic-review', approved_at=inputs['checked_at'],
                          root_cause_category='reference-evidence', root_cause=ROOT_CAUSE,
                          regression_ids=REGRESSIONS)
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes()
                      for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('linked_destination', ['receipt', 'candidate', 'canonical'])
def test_dangling_output_link_is_not_followed(tmp_path, linked_destination):
    inputs = create_promotion_inputs(tmp_path)
    destinations = {name: tmp_path / (name + '.json')
                    for name in ('receipt', 'candidate', 'canonical')}
    unexpected = tmp_path / 'unexpected-created.json'
    destinations[linked_destination].symlink_to(unexpected)
    original = inputs['dataset_path'].read_bytes()
    with pytest.raises(ValueError, match='immutable'):
        prepare_promotion(dataset_path=inputs['dataset_path'], captures=inputs['captures'],
                          output=destinations['receipt'], candidate_output=destinations['candidate'],
                          full_canonical_output=destinations['canonical'],
                          browser_observations=inputs['observations_path'],
                          approved_by='synthetic-review', approved_at=inputs['checked_at'],
                          root_cause_category='reference-evidence', root_cause=ROOT_CAUSE,
                          regression_ids=REGRESSIONS)
    assert not unexpected.exists()
    assert destinations[linked_destination].is_symlink()
    assert inputs['dataset_path'].read_bytes() == original
    assert all(not path.exists() for name, path in destinations.items() if name != linked_destination)


def test_promotion_commands_default_to_repository_paths_from_any_directory(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(reference_freeze, 'prepare_promotion',
                        lambda **kwargs: calls.setdefault('prepare', kwargs) and ({'changes': []}, b''))
    monkeypatch.setattr(reference_freeze, 'promote', lambda **kwargs: calls.setdefault('promote', kwargs) and {})
    reference_freeze.prepare_promotion_main([
        '--captures', 'captures', '--output', 'receipt.json', '--approved-by', 'reviewer',
        '--approved-at', '2026-01-01T00:00:00Z', '--root-cause-category', 'reference-evidence',
        '--root-cause', ROOT_CAUSE, '--regression-id', REGRESSIONS[0]])
    reference_freeze.promote_main([
        '--candidate', 'candidate.json', '--captures', 'captures', '--receipt', 'receipt.json',
        '--receipt-target', 'target.json'])
    root = reference_freeze.ROOT
    assert calls['prepare']['dataset_path'] == root / 'evaluation/dataset.json'
    assert calls['promote']['canonical_path'] == root / 'evaluation/dataset.json'
    assert calls['promote']['research_root'] == root / 'research/reference-freeze-promotions'
