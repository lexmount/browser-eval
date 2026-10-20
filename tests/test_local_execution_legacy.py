"""Retained execution plans remain strict without entering new cohort validation."""
from harness.web_fetch import local_execution_contract
import copy
import hashlib
import json

import pytest

from harness.web_fetch import local_execution_legacy
from harness.web_fetch.local_execution_legacy import require_legacy_execution_segments


def bind(root, name, value, *, lines=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ''.join(json.dumps(row) + '\n' for row in value) if lines else json.dumps(value)
    path.write_text(text)
    return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def legacy_inputs():
    return {'cases': [{'id': c} for c in 'abc'], 'timeout_seconds': 45,
            'validate_record_correction': local_execution_contract._require_local_record_correction}


@pytest.fixture
def execution(tmp_path):
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
    require_legacy_execution_segments(*execution, **legacy_inputs())


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
        require_legacy_execution_segments(root, contract, batches, records, **legacy_inputs())


def test_unknown_legacy_plan_is_rejected(execution):
    root, contract, batches, records = execution
    contract['execution_plans']['future'] = contract['execution_plans']['resumed']
    with pytest.raises(ValueError, match='plans are incomplete'):
        require_legacy_execution_segments(root, contract, batches, records, **legacy_inputs())


def test_fresh_cohort_validates_without_legacy_plan_decoder(tmp_path, monkeypatch):
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers import build_provider
    from harness.web_fetch.runner import run_evaluation

    def unexpected_legacy(*args, **kwargs):
        pytest.fail('A new cohort entered the historical execution decoder')

    monkeypatch.setattr(local_execution_legacy, 'require_legacy_execution_segments', unexpected_legacy)
    dataset = {'tool': 'web_fetch', 'cases': [{'id': 'fresh', 'name': 'Fresh task', 'track': 'web', 'category': 'fixture',
                         'url': 'https://example.invalid/', 'expected_any': ['fixture']}]}
    dataset_binding = bind(tmp_path, 'dataset.json', dataset)
    config_binding = bind(tmp_path, 'providers.json', {'providers': {'fixture': {'type': 'fixture'}}})
    _, cases = load_dataset(tmp_path / 'dataset.json')
    output = tmp_path / 'evidence'
    records, _ = run_evaluation(
        output_dir=output, dataset_meta=dataset, dataset_path=tmp_path / 'dataset.json',
        cases=cases, providers=[build_provider('fixture', {'type': 'fixture'})],
        provider_config_path=tmp_path / 'providers.json', runs=1, timeout=12.5,
        delay=0, max_failure_retries=0)
    batch = json.loads((output / 'manifest.json').read_text())
    contract = {'schema_version': 1, 'kind': 'uniform_native_local_measurement',
                'dataset_sources': [dataset_binding], 'providers': ['fixture'],
                'runs': 1, 'timeout_seconds': 12.5, 'max_failure_retries': 0,
                'runtime_source_sha256': batch['runtime_source_sha256'],
                'provider_config_sha256': config_binding['sha256']}
    monkeypatch.setattr(local_execution_contract, 'ROOT', tmp_path)
    local_execution_contract.require_local_execution_contract(output, dataset, records, contract, {'batches': [batch]})
    changed = copy.deepcopy(batch)
    changed['runtime_source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='execution runtime differs'):
        local_execution_contract.require_local_execution_contract(output, dataset, records, contract, {'batches': [changed]})
