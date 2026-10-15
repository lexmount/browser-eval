"""A replacement must retain old calls and measure the full new method scope."""
import copy
import hashlib
import json

import pytest

from harness.web_fetch.local_revision import require_local_task_replacement


def setup_replacement(tmp_path):
    from datetime import datetime, timezone
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers import build_provider
    from harness.web_fetch.runner import run_evaluation
    before = {'cases': [{'id': 'old', 'track': 'web'}, {'id': 'keep', 'track': 'web'}],
              'coverage_plan': {'web': ['a', 'b']}}
    added = {'id': 'new', 'name': 'New fixture', 'track': 'web', 'category': 'fixture',
             'url': 'https://example.invalid/', 'expected_any': ['fixture']}
    dataset = {**before, 'cases': [added, before['cases'][1]]}
    def row(p, c):
        return {'attempt_id': p+'__'+c, 'provider': p, 'case_id': c,
                'run': 1, 'metrics': {'scoring_source_sha256': 'scorer-'+c}}
    previous = [row(p, c) for p in ['a', 'b'] for c in ['old', 'keep']]
    def save(name, data, lines=False):
        raw = (''.join(json.dumps(r)+'\n' for r in data) if lines else json.dumps(data)).encode()
        (tmp_path/name).write_bytes(raw)
        return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}
    input_binding = save('frozen.json', {'tool': 'web_fetch', 'cases': [added]})
    providers = save('providers.json', {'providers': {p: {'type': 'fixture'} for p in ['a', 'b']}})
    network = save('network.json', {'dns_mode': 'system', 'hosts': [], 'usable_for_guarded_local_comparison': True})
    source = save('source.html', '<p>fixture</p>')
    reference = save('reference.txt', 'fixture')
    def capture(stage):
        observation = save(stage+'.json', {'case_id': 'new', 'url': added['url'],
            'independent': True, 'status': 200, 'sha256': source['sha256'],
            'reference_sha256': reference['sha256'], 'extracted_sha256': reference['sha256'],
            'checked_at': datetime.now(timezone.utc).isoformat()})
        return {'evidence': observation, 'source': source, 'reference': reference, 'extracted': reference}
    pre = capture('pre')
    _, cases = load_dataset(tmp_path/'frozen.json')
    calls, batches = [], []
    for method in ['a', 'b']:
        rows, _ = run_evaluation(output_dir=tmp_path,
            dataset_meta={'tool': 'web_fetch', 'cases': [added]}, dataset_path=tmp_path/'frozen.json',
            cases=cases, providers=[build_provider(method, {'type': 'fixture'})],
            provider_config_path=tmp_path/'providers.json', runs=1, timeout=45, delay=0)
        batch = json.loads((tmp_path/'manifest.json').read_text())
        calls.extend(rows)
        batches.append(batch)
    post = capture('post')
    contract = {'schema_version': 1, 'kind': 'uniform_native_local_measurement',
        'providers': ['a', 'b'], 'runs': 1, 'timeout_seconds': 45, 'max_failure_retries': 0,
        'dataset_storage': 'evidence', 'dataset_sources': [input_binding],
        'provider_config_sha256': providers['sha256'],
        'runtime_source_sha256': batches[0]['runtime_source_sha256'],
        'environment': batches[0]['environment'], 'parameters': batches[0]['parameters'],
        'runner_inputs': {'dataset': input_binding, 'providers': providers, 'network': network,
            'source_observations': {'new': {'pre': pre, 'post': post}}}}
    receipt = {'kind': 'local_task_replacement', 'reason': 'private DNS address',
               'removed_case_id': 'old', 'added_case_id': 'new', 'provider_calls_added': 2}
    receipt['baseline_dataset'] = save('before.json', before)
    receipt['baseline_results'] = save('previous.jsonl', previous, True)
    receipt['cohort_dataset'] = save('cohort.json', {'cases': [added]})
    receipt['cohort_results'] = save('calls.jsonl', calls, True)
    receipt['execution_contract'] = save('execution-contract.json', contract)
    receipt['cohort_execution'] = save('execution.json', {'batches': batches})
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
                        'a__new__r001': 1, 'b__new__r001': 1})
    logical = Counter(r['attempt_id'] for r in records)
    if defect == 'missing_history':
        del physical['a__old']
    elif defect == 'extra_call':
        physical['a__new__r001'] += 1
    elif defect == 'old_task_counted':
        logical['a__old'] = 1
    if defect:
        with pytest.raises(ValueError, match='inventory differs'):
            require_replacement_execution(tmp_path, binding, physical, logical)
    else:
        require_replacement_execution(tmp_path, binding, physical, logical)


@pytest.mark.parametrize('defect', ['missing_contract', 'missing_inputs', 'environment', 'timeout',
    'config', 'dns', 'source', 'late_precheck', 'output'])
def test_replacement_requires_full_physical_execution_evidence(tmp_path, defect):
    binding, dataset, records = setup_replacement(tmp_path)
    receipt = json.loads((tmp_path/'receipt.json').read_text())
    if defect == 'missing_contract':
        receipt.pop('execution_contract')
    elif defect in {'missing_inputs', 'environment', 'timeout', 'config'}:
        contract = json.loads((tmp_path/'execution-contract.json').read_text())
        if defect == 'missing_inputs': contract.pop('runner_inputs')
        elif defect == 'environment': contract['environment']['python'] = 'wrong'
        elif defect == 'timeout': contract['timeout_seconds'] = 44
        else: contract['provider_config_sha256'] = '0'*64
        (tmp_path/'execution-contract.json').write_text(json.dumps(contract))
        receipt['execution_contract']['sha256'] = hashlib.sha256((tmp_path/'execution-contract.json').read_bytes()).hexdigest()
    elif defect == 'dns': (tmp_path/'network.json').write_text('{}')
    elif defect == 'source': (tmp_path/'source.html').write_text('wrong page')
    elif defect == 'late_precheck':
        observation = json.loads((tmp_path/'pre.json').read_text())
        observation['checked_at'] = '2099-01-01T00:00:00+00:00'
        (tmp_path/'pre.json').write_text(json.dumps(observation))
    else: (tmp_path/records[-1]['artifact_path']).write_text('changed output')
    (tmp_path/'receipt.json').write_text(json.dumps(receipt))
    binding['sha256'] = hashlib.sha256((tmp_path/'receipt.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        require_local_task_replacement(tmp_path, binding, dataset, records)
