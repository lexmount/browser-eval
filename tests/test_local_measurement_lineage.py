"""Frozen inputs and physical calls must survive report assembly unchanged."""
import copy
import hashlib
import json

import pytest

from harness.web_fetch import local_audit
from harness.web_fetch.reliability import require_reliable


def bind(root, name, value, *, lines=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ''.join(json.dumps(row) + '\n' for row in value) if lines else json.dumps(value)
    path.write_text(text)
    return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def test_unknown_verdict_can_be_shown_locally_without_becoming_publishable():
    dataset = {'comparison': {'kind': 'uniform_native_local_measurement',
                              'publication_state': 'local_audit_only_not_for_release'},
               'coverage_plan': {'web': ['p']}, 'cases': [{'id': 'a', 'track': 'web'}]}
    row = {'provider': 'p', 'case_id': 'a', 'metrics': {'page_identity_required': True,
           'page_identity_status': 'unavailable', 'request_ok': True}}
    reliability = local_audit.require_complete_local_inventory(dataset, [row])
    assert reliability['present_calls'] == 1 and reliability['judged_calls'] == 0
    assert not reliability['complete']
    with pytest.raises(ValueError):
        require_reliable(dataset, [row])
    for records in [[], [row, row], [{**row, 'case_id': 'other'}]]:
        with pytest.raises(ValueError):
            local_audit.require_complete_local_inventory(dataset, records)
    dataset['comparison']['publication_state'] = 'certified'
    with pytest.raises(ValueError):
        local_audit.require_complete_local_inventory(dataset, [row])


def test_multiple_frozen_sources_preserve_order_and_reject_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_audit, 'ROOT', tmp_path)
    a = bind(tmp_path, 'first.json', {'cases': [{'id': 'a'}, {'id': 'b'}]})
    b = bind(tmp_path, 'second.json', {'cases': [{'id': 'c'}]})
    contract = {'dataset_sources': [a, b]}
    assert local_audit.local_measurement_cases(contract) == [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]
    for selection in [['b', 'a'], ['a', 'a'], ['missing']]:
        changed = copy.deepcopy(contract)
        changed['dataset_sources'][0]['case_ids'] = selection
        with pytest.raises(ValueError):
            local_audit.local_measurement_cases(changed)
    changed = copy.deepcopy(contract)
    changed['dataset_sources'].append(a)
    with pytest.raises(ValueError):
        local_audit.local_measurement_cases(changed)
    bind(tmp_path, 'second.json', {'cases': [{'id': 'changed'}]})
    with pytest.raises(ValueError):
        local_audit.local_measurement_cases(contract)


@pytest.fixture
def execution(tmp_path, monkeypatch):
    monkeypatch.setattr(local_audit, 'local_measurement_cases', lambda _: [{'id': c} for c in 'abc'])
    original_config = {'providers': {'p': {'command': ['{harness_root}/scripts/oss/p.py'], 'max_bytes': 20_000_000}, 'q': {'version': 'fixed'}}}
    migrated_config = copy.deepcopy(original_config)
    migrated_config['providers']['p']['command'] = ['{harness_root}/harness/web_fetch/workers/p.py']
    old_config = bind(tmp_path, 'original-config.json', original_config)
    new_config = bind(tmp_path, 'migrated-config.json', migrated_config)
    policy = {'runs': 1, 'timeout': 45, 'delay': .5, 'provider_concurrency': 1, 'max_failure_retries': 0}
    original = {'kind': 'uniform_native_local_measurement', 'providers': ['p', 'q'], 'dataset_sources': [], 'scoring_source_sha256': 'd' * 64, 'runs': 1, 'timeout_seconds': 45, 'delay': .5, 'max_failure_retries': 0, 'runtime_source_sha256': 'a' * 64, 'provider_config_sha256': old_config['sha256']}
    resumed = {'kind': 'post_merge_resume_and_runtime_correction', 'parameters': policy, 'case_ids_by_provider': {'p': ['b', 'c'], 'q': ['a', 'b', 'c']}, 'runtime_source_sha256': 'b' * 64, 'provider_config_sha256': new_config['sha256']}
    plans = {'original': bind(tmp_path, 'original-plan.json', original), 'resumed': bind(tmp_path, 'resumed-plan.json', resumed)}
    scheduling = {'max_parallel': 2, 'max_local': 1, 'max_cloud': 1}
    admitted = {'kind': 'bounded_admission_continuation', 'predecessor_plan_sha256': plans['resumed']['sha256'], 'parameters': policy, 'providers': ['p', 'q'], 'case_ids_by_provider': {'p': ['c'], 'q': ['c']}, 'runtime_source_sha256': 'c' * 64, 'provider_config_sha256': new_config['sha256'], 'scheduling': scheduling}
    plans['admitted'] = bind(tmp_path, 'admitted-plan.json', admitted)
    for key in plans:
        plans[key]['configuration'] = old_config if key == 'original' else new_config
    contract = {**original, 'execution_plans': plans}
    values = {'original': original, 'resumed': resumed, 'admitted': admitted}
    batches, records = [], []
    for name in ['p', 'q']:
        segments, method_rows = [], []
        chunks = [('original', 'a'), ('resumed', 'b'), ('admitted', 'c')] if name == 'p' else [('resumed', 'a'), ('resumed', 'b'), ('admitted', 'c')]
        for index, (key, case) in enumerate(chunks):
            row = {'attempt_id': f'{name}__{case}__r001', 'provider': name, 'case_id': case, 'run': 1, 'ok': True, 'elapsed_ms': 7, 'metrics': {}}
            timeout = name == 'q' and case == 'a'
            if timeout:
                row.update(ok=False, error_type='timeout', elapsed_ms=45_000)
            method_rows.append(row)
            providers = [{'name': name, 'type': 'fixture'}]
            manifest = {'providers': providers, 'parameters': policy, 'runtime_source_sha256': values[key]['runtime_source_sha256'], 'provider_config_sha256': values[key]['provider_config_sha256'], 'completed_at': 'complete', 'attempt_ids': [row['attempt_id']], 'attempt_count': 1, 'unexecuted': []}
            if timeout:
                manifest['unexecuted'] = [{'after_attempt_id': row['attempt_id'], 'reason': 'non_cancellable_call_still_running; remaining provider cases not executed'}]
            if key == 'admitted':
                manifest['scheduling'] = scheduling
            stem = f'{name}-{index}'
            segment = {'plan_id': key, 'manifest': bind(tmp_path, stem + '-manifest.json', manifest), 'records': bind(tmp_path, stem + '-rows.jsonl', [row], lines=True), 'providers': providers, 'parameters': policy, 'attempt_ids': [row['attempt_id']], 'attempt_count': 1}
            if name == 'q' and case == 'b':
                continuation = {'kind': 'resume_unexecuted_after_process_exit', 'method': name, 'resumed_plan_sha256': plans[key]['sha256'], 'parameters': policy, 'provider_config_sha256': values[key]['provider_config_sha256'], 'runtime_source_sha256': values[key]['runtime_source_sha256'], 'case_ids': ['b', 'c']}
                segment['continuation_plan'] = bind(tmp_path, 'continuation.json', continuation)
            segments.append(segment)
        records.extend(method_rows)
        batches.append({'providers': [{'name': name}], 'segments': segments, 'attempt_ids': [row['attempt_id'] for row in method_rows], 'attempt_count': 3})
    return tmp_path, contract, batches, records


def test_retained_calls_full_method_correction_and_three_runtime_phases(execution):
    local_audit._require_local_execution_segments(*execution)


@pytest.mark.parametrize('change', ['omit_timeout', 'alter_latency', 'repeat_method', 'alter_parameters', 'wrong_runtime', 'alter_scheduling', 'alter_product_config'])
def test_segment_assembly_rejects_protocol_or_evidence_changes(execution, change):
    root, contract, batches, records = copy.deepcopy(execution)
    if change == 'omit_timeout':
        batches[1]['segments'].pop(0)
    elif change == 'alter_latency':
        records[0]['elapsed_ms'] = 1
    elif change == 'repeat_method':
        batches.append(copy.deepcopy(batches[0]))
    elif change == 'alter_parameters':
        batches[0]['segments'][0]['parameters']['timeout'] = 90
    elif change in {'wrong_runtime', 'alter_scheduling'}:
        segment = batches[0]['segments'][-1]
        manifest = json.loads((root / segment['manifest']['path']).read_text())
        if change == 'wrong_runtime':
            manifest['runtime_source_sha256'] = 'wrong'
        else:
            manifest['scheduling'] = {'max_parallel': 10}
        segment['manifest'] = bind(root, 'changed-manifest.json', manifest)
    else:
        config = json.loads((root / contract['execution_plans']['resumed']['configuration']['path']).read_text())
        config['providers']['p']['max_bytes'] = 1
        binding = bind(root, 'changed-config.json', config)
        for key in ['resumed', 'admitted']:
            plan = json.loads((root / contract['execution_plans'][key]['path']).read_text())
            plan['provider_config_sha256'] = binding['sha256']
            if key == 'admitted':
                plan['predecessor_plan_sha256'] = contract['execution_plans']['resumed']['sha256']
            replacement = bind(root, f'changed-{key}-plan.json', plan)
            replacement['configuration'] = binding
            contract['execution_plans'][key] = replacement
    with pytest.raises(ValueError, match='beyond worker paths' if change == 'alter_product_config' else None):
        local_audit._require_local_execution_segments(root, contract, batches, records)
