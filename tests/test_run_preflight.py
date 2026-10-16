"""Reject unusable execution scopes before adapter validation or acquisition."""
import json
from dataclasses import replace

import pytest

from harness.web_fetch.config import load_dataset
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation


class ObservedProvider(Provider):
    def __init__(self, events):
        super().__init__('observed', {'type': 'fixture'})
        self.events = events

    def validate(self):
        self.events.append('validate')
        return []

    def fetch(self, case, timeout):
        self.events.append(case.id)
        return ProviderResult(True, 'Expected content', status_code=200)


def execute(root, events, **overrides):
    case = EvalCase('one', 'One', 'https://example.invalid/', 'synthetic', ['Expected'])
    settings = dict(output_dir=root / 'run', dataset_meta={'id': 'synthetic'},
                    dataset_path=root / 'dataset.json', cases=[case],
                    providers=[ObservedProvider(events)],
                    provider_config_path=root / 'providers.json',
                    runs=1, timeout=1, delay=0)
    settings.update(overrides)
    return run_evaluation(**settings)


@pytest.mark.parametrize('overrides', [
    {'runs': 0}, {'runs': -1}, {'runs': True}, {'runs': 1.5}, {'runs': None},
    {'timeout': 0}, {'timeout': float('inf')}, {'timeout': float('nan')},
    {'timeout': True}, {'timeout': '1'}, {'timeout': None}, {'timeout': -1},
    {'delay': -1}, {'delay': float('inf')}, {'delay': float('nan')},
    {'delay': True}, {'delay': '0'}, {'delay': None},
    {'provider_concurrency': 0}, {'provider_concurrency': 1.5},
    {'provider_concurrency': True}, {'provider_concurrency': float('inf')},
    {'max_failure_retries': True}, {'max_failure_retries': 1.0}, {'max_failure_retries': -1},
    {'resource_sample_interval_ms': 9}, {'resource_sample_interval_ms': 10.5},
    {'resource_sample_interval_ms': True},
    {'cases': []}, {'providers': []},
])
def test_bad_execution_parameters_have_no_side_effects(tmp_path, overrides):
    events = []
    with pytest.raises(ValueError):
        execute(tmp_path, events, **overrides)
    assert events == []
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('damage', ['duplicate-case', 'duplicate-provider',
                                 'unknown-kind', 'unknown-track', 'invalid-url'])
def test_bad_scope_is_rejected_before_adapter_validation(tmp_path, damage):
    events = []
    case = EvalCase('one', 'One', 'https://example.invalid/', 'synthetic', ['Expected'])
    overrides = {
        'duplicate-case': {'cases': [case, case]},
        'duplicate-provider': {'providers': [ObservedProvider(events), ObservedProvider(events)]},
        'unknown-kind': {'cases': [replace(case, task_kind='download')]},
        'unknown-track': {'cases': [replace(case, track='unregistered')]},
        'invalid-url': {'cases': [replace(case, url='example.invalid')]},
    }[damage]
    with pytest.raises(ValueError):
        execute(tmp_path, events, **overrides)
    assert events == []
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('change', [
    {'task_kind': 'download'}, {'track': 'unregistered'}, {'url': 'example.invalid'},
    {'id': '../outside'},
    {'name': ''}, {'name': 1}, {'category': []},
    {'expected_any': 'Expected'}, {'expected_any': [None]}, {'expected_any': ['']},
])
def test_dataset_validation_rejects_unexecutable_tasks(tmp_path, change):
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps({'tool': 'web_fetch', 'cases': [{
        'id': 'one', 'name': 'One', 'url': 'https://example.invalid/',
        'category': 'synthetic', 'expected_any': ['Expected'], **change}]}))
    with pytest.raises(ValueError):
        load_dataset(path)


@pytest.mark.parametrize('payload', [[], {'tool': 'web_fetch', 'cases': {}},
                                   {'tool': 'web_fetch', 'cases': [None]},
                                   {'tool': 'web_fetch', 'cases': [{'id': 'incomplete'}]}])
def test_invalid_dataset_shape_has_a_configuration_error(tmp_path, payload):
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_dataset(path)


def test_valid_repeated_execution_keeps_every_call(tmp_path):
    events = []
    records, _ = execute(tmp_path, events, runs=2, timeout=1.0, delay=0.0,
                         provider_concurrency=2, max_failure_retries=0)
    assert events == ['validate', 'one', 'one']
    assert len(records) == 2
    assert [row['run'] for row in records] == [1, 2]
    assert all(row['ok'] and row['metrics']['content_pass'] for row in records)
