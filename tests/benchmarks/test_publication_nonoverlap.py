"""Current viewers and bundles have one owner for each benchmark population."""
import copy
import json
import tarfile

import pytest

from harness.benchmarks import data
from harness.benchmarks.publication import DatasetRelease, digest, encoded


def fixture_release(track, populations, *, current=None, schema=None):
    files = {'LICENSE': b'Original notice\n'}
    datasets = {}
    for identity, ids in populations.items():
        cases = [{'id': 'case-' + str(i), 'query': 'Question ' + str(i), 'language': 'en'} for i in ids]
        raw = encoded({'cases': cases})
        path = 'datasets/' + identity + '.json'
        files['definitions/' + path] = raw
        datasets[identity] = {'path': path, 'sha256': digest(raw), 'count': len(cases)}
    registry = {'datasets': datasets}
    if current is not None:
        registry['current'] = current
    if schema is not None:
        registry['schema'] = schema
    files['definitions/registry.json'] = encoded(registry)
    return DatasetRelease(track, 'a' * 40, files, language_annotations={})


def search_release(monkeypatch):
    release = fixture_release('websearch', {'scenarios-v1': [1, 2], 'scenarios-v2': [1, 2], 'factual-v1': [3, 4]},
                              current={'scenarios': 'scenarios-v2', 'factual': 'factual-v1'},
                              schema='lexbench-websearch-registry/1')
    release.files['definitions/factual-source.json'] = b'{"original_source":"unchanged"}\n'
    supplied = release.datasets()['factual-v1']
    provider = {'repository': 'Lexmount/LexBench-SimpleQA-Verified', 'revision': 'b' * 40,
                'datasets': {'factual-v1': copy.deepcopy(supplied)},
                'files': {name: {'sha256': digest(raw), 'size': len(raw)} for name, raw in release.files.items()
                          if name in {'definitions/' + supplied['path'], 'definitions/factual-source.json'}}}
    monkeypatch.setattr(data, 'binding', lambda track: provider if track == 'simpleqa-verified' else None)
    return release, provider


def test_search_current_view_and_archive_delegate_factual_records(tmp_path, monkeypatch):
    release, provider = search_release(monkeypatch)
    original = copy.deepcopy(release.files)
    manifest = release.build(tmp_path / 'release')
    assert release.files == original
    assert manifest['datasets'] == release.datasets()
    assert manifest['dataset_sources'] == {'factual-v1': 'simpleqa-verified'}
    assert [(row['config'], row['count']) for row in manifest['viewer']['configurations']] == [('scenarios-v2', 2)]
    for path in ('definitions/datasets/factual-v1.json', 'definitions/factual-source.json'):
        assert path not in manifest['files']
        assert not (tmp_path / 'release' / path).exists()
    with tarfile.open(tmp_path / 'release/definitions.tar.gz') as archive:
        names = set(archive.getnames())
        assert 'definitions/datasets/factual-v1.json' not in names
        assert 'definitions/factual-source.json' not in names
        assert all(archive.extractfile(name).read() == (tmp_path / 'release' / name).read_bytes() for name in names)
    # The historic scenario definition and exact original registry remain reproducible.
    for path in ('definitions/registry.json', 'definitions/datasets/scenarios-v1.json'):
        assert (tmp_path / 'release' / path).read_bytes() == original[path]
    before = {'files': {name: {'sha256': digest(raw), 'size': len(raw)} for name, raw in original.items()},
              'datasets': release.datasets(), 'dataset_sources': {'factual-v1': 'simpleqa-verified'},
              'external_sources': manifest['external_sources']}
    after = {**before, 'files': manifest['files']}
    assert data._definition_bindings(before) == data._definition_bindings(after)
    # A rebuilt already-delegated source does not need the absent local copies.
    filtered = {name: raw for name, raw in release.files.items() if name not in release.external_definitions()}
    rebuilt = DatasetRelease('websearch', 'a' * 40, filtered, language_annotations={},
                             external_sources=manifest['external_sources'])
    assert list(rebuilt.records()) == list(release.records())
    assert rebuilt.build(tmp_path / 'second')['datasets'] == manifest['datasets']


@pytest.mark.parametrize('change', ['descriptor', 'copied-case', 'copied-receipt', 'missing-provider-file', 'provider-file'])
def test_external_composition_fails_closed(tmp_path, monkeypatch, change):
    release, provider = search_release(monkeypatch)
    if change == 'descriptor':
        provider['datasets']['factual-v1']['sha256'] = 'c' * 64
    elif change == 'copied-case':
        release.files['definitions/datasets/factual-v1.json'] += b' '
    elif change == 'copied-receipt':
        release.files['definitions/factual-source.json'] += b' '
    elif change == 'missing-provider-file':
        provider['files'].pop('definitions/factual-source.json')
    else:
        provider['files']['definitions/datasets/factual-v1.json']['sha256'] = 'c' * 64
    with pytest.raises(ValueError, match='External'):
        release.build(tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('change', ['unknown-target', 'missing-key', 'unknown-schema', 'missing-schema'])
def test_real_current_registry_never_falls_back_to_all_datasets(tmp_path, monkeypatch, change):
    release, _ = search_release(monkeypatch)
    registry = json.loads(release.files['definitions/registry.json'])
    if change == 'unknown-target':
        registry['current']['scenarios'] = 'unregistered'
    elif change == 'missing-key':
        registry['current'].pop('scenarios')
    elif change == 'unknown-schema':
        registry['schema'] = 'unknown/2'
    else:
        registry.pop('schema')
    release.files['definitions/registry.json'] = encoded(registry)
    with pytest.raises(ValueError, match='viewer'):
        release.build(tmp_path / 'bad')


def test_webarena_current_view_counts_full_scope_once_retains_all_variants(tmp_path):
    release = fixture_release('webarena-replay', {'human-qualified-v1': [1], 'corrected-90-v1': [1, 2],
                                                'full-812-v1': [1, 2, 3]}, schema='lex_replay_dataset_registry/1')
    manifest = release.build(tmp_path / 'release')
    assert [(row['config'], row['count']) for row in manifest['viewer']['configurations']] == [('full-812-v1', 3)]
    with tarfile.open(tmp_path / 'release/definitions.tar.gz') as archive:
        for name, raw in release.files.items():
            assert archive.extractfile(name).read() == raw
    assert set(manifest['datasets']) == {'human-qualified-v1', 'corrected-90-v1', 'full-812-v1'}


def test_noncurrent_definition_integrity_remains_enforced(tmp_path):
    release = fixture_release('webarena-replay', {'human-qualified-v1': [1], 'full-812-v1': [1, 2]},
                              schema='lex_replay_dataset_registry/1')
    release.files['definitions/datasets/human-qualified-v1.json'] += b' '
    with pytest.raises(ValueError, match='hash mismatch'):
        release.build(tmp_path / 'bad')
