"""Frozen inputs and physical calls must survive report assembly unchanged."""
from harness.web_fetch import local_execution_contract
import copy
import hashlib
import json

import pytest

from harness.web_fetch import local_audit
from harness.web_fetch.reliability import require_reliable


@pytest.mark.parametrize('mode', ['system', 'public_doh'])
def test_local_dns_matches_frozen_guarded_configuration(mode):
    config = {'providers': {'local': {'type': 'subprocess_jsonl',
        'method_type': 'deepseek_harness_builtin', 'dns_mode': mode},
        'remote': {'type': 'tavily_extract'}}}
    assert local_execution_contract._require_bound_dns_mode(
        config, ['local', 'remote'], {'dns_mode': mode}) == mode
    other = 'system' if mode == 'public_doh' else 'public_doh'
    with pytest.raises(ValueError, match='DNS differs'):
        local_execution_contract._require_bound_dns_mode(
            config, ['local', 'remote'], {'dns_mode': other})
    config['providers']['second'] = {'type': 'moli_webfetch', 'dns_mode': other}
    with pytest.raises(ValueError, match='DNS differs'):
        local_execution_contract._require_bound_dns_mode(
            config, ['local', 'second'], {'dns_mode': mode})


def test_local_dns_preserves_default_and_rejects_unknown_mode():
    config = {'providers': {'local': {'type': 'moli_webfetch'}}}
    assert local_execution_contract._require_bound_dns_mode(
        config, ['local'], {'dns_mode': 'system'}) == 'system'
    with pytest.raises(ValueError, match='dns_mode must'):
        local_execution_contract._require_bound_dns_mode(
            config, ['local'], {'dns_mode': 'unchecked'})


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
    row = {'provider': 'p', 'case_id': 'a', 'metrics': {'rule_review_status': 'unresolved', 'request_ok': True}}
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
    monkeypatch.setattr(local_execution_contract, 'ROOT', tmp_path)
    a = bind(tmp_path, 'first.json', {'cases': [{'id': 'a'}, {'id': 'b'}]})
    b = bind(tmp_path, 'second.json', {'cases': [{'id': 'c'}]})
    contract = {'dataset_sources': [a, b]}
    assert local_execution_contract.local_measurement_cases(contract) == [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]
    for selection in [['b', 'a'], ['a', 'a'], ['missing']]:
        changed = copy.deepcopy(contract)
        changed['dataset_sources'][0]['case_ids'] = selection
        with pytest.raises(ValueError):
            local_execution_contract.local_measurement_cases(changed)
    changed = copy.deepcopy(contract)
    changed['dataset_sources'].append(a)
    with pytest.raises(ValueError):
        local_execution_contract.local_measurement_cases(changed)
    bind(tmp_path, 'second.json', {'cases': [{'id': 'changed'}]})
    with pytest.raises(ValueError):
        local_execution_contract.local_measurement_cases(contract)
