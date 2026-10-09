"""Candidate and receipt destinations must not overwrite one another."""
import pytest

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
