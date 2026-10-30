"""Published and pinned definitions retain all committed, registry-bound inputs."""
import hashlib
import json
from dataclasses import replace

import pytest

from harness.benchmarks.publication import DatasetRelease
from tests.benchmarks.test_publication import checkout, git


def supporting_source(tmp_path):
    root, _, _ = checkout(tmp_path, 'webfetch')
    prefix = root / 'src/lexbench_webfetch/definitions'
    registry = json.loads((prefix / 'registry.json').read_bytes())
    original = b'{"source":"$Format:%H$","contract":"unchanged"}\n'
    for name in ('dataset-catalog.json', 'source-inputs.json'):
        (prefix / 'evaluation' / name).write_bytes(original)
        registry['suites'][0]['files'][name] = hashlib.sha256(original).hexdigest()
    (prefix / 'registry.json').write_text(json.dumps(registry))
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Supporting definition contracts')
    return root, git(root, 'rev-parse', 'HEAD'), original


@pytest.mark.parametrize('boundary', ['datasets', 'records', 'build'])
@pytest.mark.parametrize('defect', ['changed', 'missing', 'invalid-hash', 'absolute', 'traversal'])
def test_every_declared_supporting_file_is_checked_before_admission(tmp_path, boundary, defect):
    root, revision, _ = supporting_source(tmp_path)
    release = DatasetRelease.from_git(root, 'webfetch', revision)
    files = dict(release.files)
    name = 'definitions/evaluation/source-inputs.json'
    registry = json.loads(files['definitions/registry.json'])
    declared = registry['suites'][0]['files']
    if defect == 'changed':
        files[name] = b'{"changed":true}'
    elif defect == 'missing':
        del files[name]
    elif defect == 'invalid-hash':
        declared['source-inputs.json'] = None
    else:
        bad = '/source-inputs.json' if defect == 'absolute' else '../source-inputs.json'
        declared[bad] = declared.pop('source-inputs.json')
    files['definitions/registry.json'] = json.dumps(registry).encode()
    release = replace(release, files=files)
    with pytest.raises(ValueError):
        if boundary == 'datasets':
            release.datasets()
        elif boundary == 'records':
            list(release.records())
        else:
            release.build(tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


@pytest.mark.parametrize('hub', [False, True])
@pytest.mark.parametrize('local_attributes', [False, True])
def test_release_uses_original_blobs_despite_export_attributes(tmp_path, hub, local_attributes):
    root, revision, original = supporting_source(tmp_path)
    if hub:
        public = tmp_path / 'hub'
        DatasetRelease.from_git(root, 'webfetch', revision).build(public)
        root = public
        git(root, 'init', '-q')
        prefix = 'definitions'
    else:
        prefix = 'src/lexbench_webfetch/definitions'
    attributes = f'NOTICE export-ignore\n{prefix}/evaluation/dataset-catalog.json export-ignore\n{prefix}/evaluation/source-inputs.json export-subst\n'
    if local_attributes:
        (root / '.git/info/attributes').write_text(attributes)
    else:
        (root / '.gitattributes').write_text(attributes)
    git(root, 'add', '.')
    git(root, 'commit', '--allow-empty', '-qm', 'Export attributes are not source bytes')
    revision = git(root, 'rev-parse', 'HEAD')
    release = DatasetRelease.from_git(root, 'webfetch', revision)
    assert release.files['NOTICE'] == b'Retain this notice.\n'
    assert release.files['definitions/evaluation/dataset-catalog.json'] == original
    assert release.files['definitions/evaluation/source-inputs.json'] == original
    release.build(tmp_path / 'release')


@pytest.mark.parametrize('materialization', ['valid', 'changed', 'symlink'])
def test_raw_blob_reader_still_requires_exact_materialized_lfs_bytes(tmp_path, materialization):
    root, _, original = supporting_source(tmp_path)
    path = root / 'src/lexbench_webfetch/definitions/evaluation/source-inputs.json'
    path.write_text('version https://git-lfs.github.com/spec/v1\noid sha256:'
                    + hashlib.sha256(original).hexdigest() + '\nsize ' + str(len(original)) + '\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Pinned LFS object')
    revision = git(root, 'rev-parse', 'HEAD')
    if materialization == 'symlink':
        path.unlink()
        path.symlink_to(path.with_name('dataset-catalog.json'))
    else:
        path.write_bytes(original if materialization == 'valid' else b'wrong materialized object')
    if materialization == 'valid':
        release = DatasetRelease.from_git(root, 'webfetch', revision)
        assert release.files['definitions/evaluation/source-inputs.json'] == original
        release.build(tmp_path / 'release')
    else:
        with pytest.raises(ValueError):
            DatasetRelease.from_git(root, 'webfetch', revision)


def test_raw_blob_reader_rejects_submodule_definitions(tmp_path):
    root, revision, _ = supporting_source(tmp_path)
    git(root, 'update-index', '--add', '--cacheinfo', '160000,' + revision
        + ',src/lexbench_webfetch/definitions/external')
    git(root, 'commit', '-qm', 'Unsupported linked definitions')
    with pytest.raises(ValueError, match='links or special files'):
        DatasetRelease.from_git(root, 'webfetch', git(root, 'rev-parse', 'HEAD'))
