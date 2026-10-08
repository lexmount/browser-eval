"""Resource comparison exports include declared evidence, never worker extras."""
import base64
import hashlib
import json
from dataclasses import asdict

import pytest

from harness.web_fetch.assembly import assemble
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.report_build import build_report
from harness.web_fetch.resource_cohort import METRICS, build_summary, package
from harness.web_fetch.runner import run_evaluation


def make_run(root, count, resource=False):
    class Fixture(Provider):
        def fetch(self, case, timeout):
            return ProviderResult(True, 'Expected', metadata={
                '_raw_response_b64': base64.b64encode(b'{"synthetic_private_envelope":true}').decode(),
                'raw_response_sha256': hashlib.sha256(b'{"synthetic_private_envelope":true}').hexdigest(),
                'raw_response_bytes': len(b'{"synthetic_private_envelope":true}'),
                'client_resource': {metric: 2 for metric in METRICS},
                'resource_environment': {
                    'backend': 'docker_cgroup_v2', 'cgroup_version': '2',
                    'network_isolation': 'one dedicated bridge with no peer containers per call',
                    'controller_excluded': True, 'image_id': 'synthetic-image',
                    'docker_architecture': 'arm64', 'cpus': 2, 'memory': '2g',
                    'pids_limit': 128, 'sample_interval_ms': 50,
                }})

    cases = [EvalCase(f'case-{index}', f'Case {index}', 'https://example.invalid/',
                     'synthetic', ['Expected']) for index in range(count)]
    dataset = {'id': 'synthetic', 'cases': [asdict(case) for case in cases],
               'coverage_plan': {'web': ['fixture']},
               'comparison': {'kind': 'headless_client_resource_profile'} if resource else {}}
    records, _ = run_evaluation(output_dir=root, dataset_meta=dataset,
        dataset_path=root.parent / 'dataset.json', cases=cases,
        providers=[Fixture('fixture', {'type': 'fixture'})],
        provider_config_path=root.parent / 'providers.json', runs=1, timeout=1, delay=0)
    for folder in ('sources', 'list-references'):
        (root / folder).mkdir(exist_ok=True)
    return records


@pytest.fixture
def runs(tmp_path):
    base, resource = tmp_path / 'base', tmp_path / 'resource'
    make_run(base, 1)
    records = make_run(resource, 5, resource=True)
    package(resource)
    (resource / 'workers').mkdir()
    (resource / 'workers/private-note.txt').write_text('synthetic private note')
    (resource / 'artifacts/unbound-debug.txt').write_text('synthetic unbound bytes')
    return base, resource, records


def test_resource_assembly_and_report_copy_only_declared_evidence(runs, tmp_path):
    base, resource, records = runs
    output = tmp_path / 'assembled'
    assemble(base, tmp_path / 'certificates', [], output, resource_cohort=resource)
    report = build_report(output, tmp_path / 'report').parent
    for target in (output, report):
        cohort = target / 'resource-cohort'
        assert not (cohort / 'workers').exists()
        assert not (cohort / 'artifacts/unbound-debug.txt').exists()
        for record in records:
            assert (cohort / record['artifact_path']).read_bytes() == b'Expected'
            private = cohort / record['provider_metadata']['raw_response_path']
            assert private.exists() == (target == output)
        assert (cohort / 'resource-comparison.json').read_bytes() == (resource / 'resource-comparison.json').read_bytes()


def test_resource_package_omits_local_paths_without_changing_config_hash(tmp_path):
    root = tmp_path / 'resource'
    make_run(root, 5, resource=True)
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['provider_config_source'] = '/home/private/resource-custom.json'
    manifest['provider_config_sha256'] = hashlib.sha256(b'custom resources').hexdigest()
    path.write_text(json.dumps(manifest))
    original = path.read_bytes()
    package(root)
    batch = json.loads((root / 'execution.json').read_text())['batches'][0]
    assert batch['provider_config_source'] == 'resource-custom.json'
    assert batch['provider_config_sha256'] == manifest['provider_config_sha256']
    assert 'hostname' not in batch['environment']
    assert path.read_bytes() == original


def test_resource_identity_conflict_does_not_write_partial_package_metadata(tmp_path):
    root = tmp_path / 'resource'
    make_run(root, 5, resource=True)
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['provider_config_sha256'] = 'a' * 64
    manifest['runtime_identity_record'] = {'provider_config_sha256': 'b' * 64}
    path.write_text(json.dumps(manifest))
    original = path.read_bytes()
    with pytest.raises(ValueError, match='Conflicting provider configuration hashes'):
        package(root)
    assert not (root / 'resource-comparison.json').exists()
    assert not (root / 'execution.json').exists()
    assert path.read_bytes() == original


@pytest.mark.parametrize('damage', ['summary-binding', 'summary-value', 'linked-artifact', 'missing-auxiliary'])
def test_invalid_resource_comparison_fails_without_exposing_output(runs, tmp_path, damage):
    base, resource, records = runs
    if damage.startswith('summary'):
        path = resource / 'resource-comparison.json'
        summary = json.loads(path.read_text())
        if damage == 'summary-binding':
            summary['bindings']['results_sha256'] = '0' * 64
        else:
            summary['rows'][0]['metrics']['cpu_ms']['mean'] = 999
        path.write_text(json.dumps(summary))
    elif damage == 'linked-artifact':
        path = resource / records[0]['artifact_path']
        actual = resource / 'same-bytes.txt'
        actual.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(actual)
    else:
        relative = 'artifacts/fixture/missing.txt'
        records[0]['provider_metadata'].update(raw_stdout_path=relative,
            raw_stdout_sha256=hashlib.sha256(b'missing bytes').hexdigest())
        (resource / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))
        summary = json.loads((resource / 'resource-comparison.json').read_text())
        summary['bindings']['results_sha256'] = hashlib.sha256((resource / 'results.jsonl').read_bytes()).hexdigest()
        (resource / 'resource-comparison.json').write_text(json.dumps(summary))
    output = tmp_path / 'rejected'
    with pytest.raises(ValueError):
        assemble(base, tmp_path / 'certificates', [], output, resource_cohort=resource)
    assert not output.exists()


@pytest.mark.parametrize('damage', ['missing-provider', 'duplicate-case', 'boolean', 'infinite', 'negative'])
def test_resource_summary_rejects_incomplete_scope_and_invalid_measurements(runs, damage):
    _, resource, records = runs
    dataset = json.loads((resource / 'dataset.json').read_text())
    batches = json.loads((resource / 'execution.json').read_text())['batches']
    if damage == 'missing-provider':
        dataset['coverage_plan']['web'].append('absent-method')
    elif damage == 'duplicate-case':
        dataset['cases'].append(dataset['cases'][0])
    else:
        records[0]['provider_metadata']['client_resource']['cpu_ms'] = {
            'boolean': True, 'infinite': float('inf'), 'negative': -1,
        }[damage]
    with pytest.raises(ValueError):
        build_summary(dataset, records, batches)
