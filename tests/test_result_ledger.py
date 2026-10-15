import copy

import pytest

from harness.web_fetch.result_ledger import build_ledger, validate_ledger


def states():
    return [{'id': 'original', 'source_results_sha256': 'a' * 64,
             'verdicts': {'p__a': 'pass', 'p__b': 'fail', 'q__a': 'pass'}},
            {'id': 'scoring', 'source_results_sha256': 'b' * 64,
             'verdicts': {'p__a': 'fail', 'p__b': 'pass', 'q__a': 'pass'}},
            {'id': 'environment', 'source_results_sha256': 'c' * 64,
             'verdicts': {'p__a': 'excluded', 'p__b': 'pass', 'q__a': 'excluded'}}]


def test_ledger_conserves_flips_and_keeps_excluded_calls_in_inventory():
    ledger = build_ledger(states())
    assert ledger['logical_results'] == 3
    assert ledger['rounds'][1]['added_passes'] == 1
    assert ledger['rounds'][1]['removed_passes'] == 1
    assert ledger['current'] == {'pass': 1, 'fail': 0, 'excluded': 2, 'unavailable': 0}
    assert validate_ledger(ledger) == ledger


@pytest.mark.parametrize('change', ['total', 'transition', 'inventory'])
def test_ledger_rejects_inconsistent_release_numbers(change):
    ledger = copy.deepcopy(build_ledger(states()))
    if change == 'total':
        ledger['current']['pass'] += 1
    elif change == 'transition':
        ledger['rounds'][1]['added_passes'] += 1
    else:
        del ledger['states'][1]['verdicts']['p__a']
    with pytest.raises(ValueError):
        validate_ledger(ledger)


def test_ledger_rejects_a_current_verdict_that_differs_from_scored_evidence():
    ledger = build_ledger([states()[0]])
    rows = [{'attempt_id': key, 'ok': True, 'metrics': {'content_pass': value == 'pass'}}
            for key, value in states()[0]['verdicts'].items()]
    validate_ledger(ledger, rows)
    rows[0]['metrics']['content_pass'] = False
    with pytest.raises(ValueError, match='current scored results'):
        validate_ledger(ledger, rows)


def replacement_states():
    return [
        {'id': 'prior', 'source_results_sha256': 'a'*64, 'verdicts': {
            'p__old': 'excluded', 'q__old': 'excluded', 'p__keep': 'pass', 'q__keep': 'fail'}},
        {'id': 'replacement', 'source_results_sha256': 'b'*64, 'verdicts': {
            'p__new': 'pass', 'q__new': 'fail', 'p__keep': 'pass', 'q__keep': 'fail'},
         'replacement': {'removed_attempt_ids': ['p__old', 'q__old'],
                         'added_attempt_ids': ['p__new', 'q__new']}}]


def test_atomic_replacement_counts_entries_and_exits_in_the_same_round():
    ledger = build_ledger(replacement_states(), predecessor={'path': 'prior.json', 'sha256': 'a'*64})
    assert ledger['rounds'][1]['before_passes'] == 1
    assert ledger['rounds'][1]['added_passes'] == 1
    assert ledger['rounds'][1]['removed_passes'] == 0
    assert len(ledger['rounds'][1]['changes']) == 4
    assert ledger['current']['pass'] == 2
    with pytest.raises(ValueError, match='predecessor evidence'):
        validate_ledger(ledger)


@pytest.mark.parametrize('defect', ['missing_event', 'missing_method', 'changed_inherited', 'wrong_provider'])
def test_atomic_replacement_rejects_unexplained_inventory_or_score_changes(defect):
    rows = replacement_states()
    if defect == 'missing_event': rows[1].pop('replacement')
    elif defect == 'missing_method': rows[1]['replacement']['added_attempt_ids'].pop()
    elif defect == 'changed_inherited': rows[1]['verdicts']['q__keep'] = 'pass'
    else:
        rows[1]['verdicts']['r__new'] = rows[1]['verdicts'].pop('q__new')
        rows[1]['replacement']['added_attempt_ids'] = ['p__new', 'r__new']
    with pytest.raises(ValueError, match='atomic complete-method transition'):
        build_ledger(rows)


def test_replacement_cannot_skip_physical_evidence_by_omitting_predecessor():
    ledger = build_ledger(replacement_states())
    with pytest.raises(ValueError, match="bound predecessor evidence"):
        validate_ledger(ledger)
