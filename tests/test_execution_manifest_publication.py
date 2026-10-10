"""Published run metadata retains provenance without disclosing host paths."""

from copy import deepcopy

import pytest

from harness.web_fetch.publication_privacy import project_execution_manifest


@pytest.mark.parametrize('source, expected, omitted', [
    ('configs/custom.json', 'configs/custom.json', False),
    ('/opt/private/custom.json', 'custom.json', True),
    (r'C:\Users\private\custom.json', 'custom.json', True),
    (r'\\private-host\share\custom.json', 'custom.json', True),
    ('C:custom.json', 'custom.json', True),
    ('../private/custom.json', 'custom.json', True),
])
def test_public_metadata_preserves_declared_file_identity(source, expected, omitted):
    manifest = {
        'provider_config_source': source, 'provider_config_sha256': 'a' * 64,
        'dataset': {'source': source, 'case_count': 2}, 'dataset_sha256': 'b' * 64,
        'runtime_source_sha256': 'c' * 64,
        'environment': {'hostname': 'private-host', 'python': '3.11'},
        'runtime_identity_record': {'version': 'fixture', 'binary_sha256': 'd' * 64},
        'attempt_ids': ['one', 'two'], 'parameters': {'runs': 1, 'timeout': 10},
    }
    original = deepcopy(manifest)
    public = project_execution_manifest(manifest)
    assert manifest == original
    assert public['provider_config_source'] == public['dataset']['source'] == expected
    assert public.get('provider_config_path_omitted', False) == omitted
    assert public['dataset'].get('source_path_omitted', False) == omitted
    assert public['environment'] == {'python': '3.11'}
    for field in ('provider_config_sha256', 'dataset_sha256', 'runtime_source_sha256',
                  'runtime_identity_record', 'attempt_ids', 'parameters'):
        assert public[field] == manifest[field]
    assert project_execution_manifest(public) == public


@pytest.mark.parametrize('manifest', [{}, {'provider_config_sha256': None},
                                      {'provider_config_source': None}])
def test_missing_configuration_provenance_stays_missing(manifest):
    assert project_execution_manifest(manifest) == manifest


@pytest.mark.parametrize('identity_hash', ['a' * 64, 'b' * 64, None])
def test_declared_runtime_and_configuration_identity_must_agree_when_known(identity_hash):
    manifest = {'provider_config_sha256': 'a' * 64,
                'runtime_identity_record': {'provider_config_sha256': identity_hash}}
    if identity_hash not in ('a' * 64, None):
        with pytest.raises(ValueError, match='Conflicting provider configuration hashes'):
            project_execution_manifest(manifest)
    else:
        assert project_execution_manifest(manifest) == manifest


def test_completed_case_projection_retains_observed_scope_and_private_manifest_locally():
    manifest = {
        'dataset': {'source': '/home/private/tasks.json', 'case_count': 3},
        'projection': {'kind': 'completed-case-from-live-preflight',
                       'source_manifest_path': '/home/private/run/manifest.json'},
        'reference_checks': {'cases': {'one': {}, 'two': {}}},
    }
    public = project_execution_manifest(manifest)
    assert public['dataset']['case_count'] == 2
    assert public['dataset']['source'] == 'tasks.json'
    assert 'source_manifest_path' not in public['projection']
    assert public['projection']['source_manifest_retained_in_local_run'] is True
    assert manifest['dataset']['case_count'] == 3
    assert manifest['projection']['source_manifest_path'] == '/home/private/run/manifest.json'
