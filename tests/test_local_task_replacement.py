"""A replacement must retain old calls and measure the full new method scope."""
import copy
import hashlib
import json

import pytest

from harness.web_fetch.local_revision import require_local_task_replacement


def setup_replacement(tmp_path):
    before = {'cases': [{'id': 'old', 'track': 'web'}, {'id': 'keep', 'track': 'web'}],
              'coverage_plan': {'web': ['a', 'b']}}
    added = {'id': 'new', 'track': 'web'}
    dataset = {**before, 'cases': [added, before['cases'][1]]}
    def row(p, c):
        return {'attempt_id': p+'__'+c, 'provider': p, 'case_id': c,
                'run': 1, 'metrics': {'scoring_source_sha256': 'scorer-'+c}}
    previous = [row(p, c) for p in ['a', 'b'] for c in ['old', 'keep']]
    calls = [row(p, 'new') for p in ['a', 'b']]
    receipt = {'kind': 'local_task_replacement', 'reason': 'private DNS address',
               'removed_case_id': 'old', 'added_case_id': 'new', 'provider_calls_added': 2}
    def save(name, data, lines=False):
        raw = (''.join(json.dumps(r)+'\n' for r in data) if lines else json.dumps(data)).encode()
        (tmp_path/name).write_bytes(raw)
        return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}
    receipt['baseline_dataset'] = save('before.json', before)
    receipt['baseline_results'] = save('previous.jsonl', previous, True)
    receipt['cohort_dataset'] = save('cohort.json', {'cases': [added]})
    receipt['cohort_results'] = save('calls.jsonl', calls, True)
    binding = save('receipt.json', receipt)
    records = [r for r in previous if r['case_id'] != 'old']+calls
    return binding, dataset, records


def test_complete_replacement_preserves_distinct_scoring_sources(tmp_path):
    binding, dataset, records = setup_replacement(tmp_path)
    result = require_local_task_replacement(tmp_path, binding, dataset, records)
    assert result == {r['attempt_id']: r['metrics']['scoring_source_sha256'] for r in records}


@pytest.mark.parametrize('defect', ['missing_method', 'changed_old_result', 'changed_case',
                                   'excluded_new_result', 'different_family', 'changed_call'])
def test_replacement_rejects_missing_or_altered_evidence(tmp_path, defect):
    binding, dataset, records = setup_replacement(tmp_path)
    dataset, records = copy.deepcopy(dataset), copy.deepcopy(records)
    if defect == 'missing_method': records.pop()
    elif defect == 'changed_old_result': records[0]['run'] = 2
    elif defect == 'changed_case': dataset['cases'][1]['url'] = 'https://wrong.example/'
    elif defect == 'excluded_new_result': records[-1]['metrics']['evaluation_excluded'] = True
    elif defect == 'different_family': dataset['cases'][0]['track'] = 'dynamic'
    elif defect == 'changed_call': records[-1]['elapsed_ms'] = 1
    with pytest.raises(ValueError):
        require_local_task_replacement(tmp_path, binding, dataset, records)


@pytest.mark.parametrize('defect', [None, 'missing_history', 'extra_call', 'old_task_counted'])
def test_replacement_execution_preserves_history_without_counting_old_task(tmp_path, defect):
    from collections import Counter
    from harness.web_fetch.report_check import require_replacement_execution
    binding, dataset, records = setup_replacement(tmp_path)
    physical = Counter({'a__old': 1, 'b__old': 1, 'a__keep': 1, 'b__keep': 1,
                        'a__new': 1, 'b__new': 1})
    logical = Counter(r['attempt_id'] for r in records)
    if defect == 'missing_history':
        del physical['a__old']
    elif defect == 'extra_call':
        physical['a__new'] += 1
    elif defect == 'old_task_counted':
        logical['a__old'] = 1
    if defect:
        with pytest.raises(ValueError, match='inventory differs'):
            require_replacement_execution(tmp_path, binding, physical, logical)
    else:
        require_replacement_execution(tmp_path, binding, physical, logical)
