import copy
import hashlib

import pytest

from harness.web_fetch.cohort_exclusions import apply_cohort_exclusions, require_cohort_exclusions
from lexbench_webfetch.metrics import task_measured, task_passed


def test_environment_exclusion_is_symmetric_and_preserves_calls(tmp_path):
    proof = tmp_path / 'dns.json'
    proof.write_text('{"address":"10.2.0.11"}')
    dataset = {'cases': [{'id': 'a'}, {'id': 'b'}], 'comparison': {'excluded_cases': [
        {'case_id': 'a', 'reason': 'Private DNS address in local environment',
         'evidence_path': 'dns.json', 'evidence_sha256': hashlib.sha256(proof.read_bytes()).hexdigest()}]}}
    records = [{'attempt_id': provider + '__' + case, 'case_id': case, 'provider': provider,
                'ok': provider == 'hosted', 'elapsed_ms': 7, 'artifact_path': 'original.txt',
                'metrics': {'content_pass': provider == 'hosted', 'content_sha256': 'a'*64}}
               for provider in ('local', 'hosted') for case in ('a', 'b')]
    before = copy.deepcopy(records)
    apply_cohort_exclusions(tmp_path, dataset, records)
    for old, new in zip(before, records):
        assert {k: v for k, v in old.items() if k != 'metrics'} == {k: v for k, v in new.items() if k != 'metrics'}
        if new['case_id'] == 'a':
            assert not task_measured(new['metrics'])
            assert not task_passed(new['ok'], new['metrics'])
        else:
            assert new == old
    records[0]['metrics']['evaluation_excluded'] = False
    with pytest.raises(ValueError, match='every method'):
        require_cohort_exclusions(tmp_path, dataset, records)
    records[0]['metrics']['evaluation_excluded'] = True
    proof.write_text('changed')
    with pytest.raises(ValueError, match='evidence changed'):
        require_cohort_exclusions(tmp_path, dataset, records)
