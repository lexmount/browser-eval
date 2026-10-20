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
