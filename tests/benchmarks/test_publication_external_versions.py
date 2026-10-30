"""Delegated Hub releases rebuild against their recorded provider, never today's pin."""
import copy
import json
from dataclasses import replace

import pytest

from harness.benchmarks import data
from harness.benchmarks.publication import DatasetRelease
from tests.benchmarks.test_publication import git
from tests.benchmarks.test_publication_nonoverlap import search_release


def hub_release(tmp_path, monkeypatch):
    release, provider = search_release(monkeypatch)
    path = tmp_path / 'hub'
    manifest = release.build(path)
    git(path, 'init', '-q')
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'Delegated Search publication')
    loaded = DatasetRelease.from_git(path, 'websearch', git(path, 'rev-parse', 'HEAD'))
    return loaded, provider, manifest, path


@pytest.mark.parametrize('changed_questions', [False, True])
def test_hub_rebuild_retains_old_provider_after_global_source_advance(tmp_path, monkeypatch, changed_questions):
    loaded, old, original, _ = hub_release(tmp_path, monkeypatch)
    newer = copy.deepcopy(old)
    newer['revision'] = 'c' * 40
    newer['files']['definitions/factual-source.json']['sha256'] = 'd' * 64
    if changed_questions:
        newer['datasets']['factual-v1']['sha256'] = 'e' * 64
    calls = []
    monkeypatch.setattr(data, 'binding', lambda track: calls.append(track) or newer)
    rebuilt = loaded.build(tmp_path / 'rebuilt')
    assert calls == []
    assert rebuilt['external_sources'] == original['external_sources'] == {'simpleqa-verified': old}
    assert rebuilt['dataset_sources'] == original['dataset_sources']
    assert (tmp_path / 'rebuilt/definitions/registry.json').read_bytes() == loaded.files['definitions/registry.json']
    assert set(rebuilt['external_sources']['simpleqa-verified']) == {'repository', 'revision', 'datasets', 'files'}


def test_legacy_delegated_hub_needs_explicit_version_context(tmp_path, monkeypatch):
    _, old, manifest, path = hub_release(tmp_path, monkeypatch)
    manifest.pop('external_sources')
    (path / 'manifest.json').write_text(json.dumps(manifest))
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'Legacy missing dependency version')
    loaded = DatasetRelease.from_git(path, 'websearch', git(path, 'rev-parse', 'HEAD'))
    with pytest.raises(ValueError, match='recorded immutable binding or explicit selections'):
        loaded.build(tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()
    corrected = loaded.build(tmp_path / 'explicit', selections={'simpleqa-verified': old})
    assert corrected['external_sources'] == {'simpleqa-verified': old}


def test_explicit_new_provider_receipt_is_serialized_for_intentional_new_edition(tmp_path, monkeypatch):
    loaded, old, _, _ = hub_release(tmp_path, monkeypatch)
    newer = copy.deepcopy(old)
    newer['revision'] = 'c' * 40
    newer['files']['definitions/factual-source.json'] = {'sha256': 'd' * 64, 'size': 500}
    built = loaded.build(tmp_path / 'new', selections={'simpleqa-verified': newer})
    assert built['external_sources'] == {'simpleqa-verified': newer}


@pytest.mark.parametrize('defect', ['revision', 'repository', 'missing-resource', 'resource-size', 'resource-hash', 'datasets-type', 'count-type', 'missing-provider'])
def test_invalid_recorded_provider_context_fails_before_output(tmp_path, monkeypatch, defect):
    loaded, provider, _, _ = hub_release(tmp_path, monkeypatch)
    context = {'simpleqa-verified': copy.deepcopy(provider)}
    selected = context['simpleqa-verified']
    if defect == 'missing-provider':
        context.clear()
    elif defect == 'missing-resource':
        selected['files'].pop('definitions/factual-source.json')
    elif defect == 'resource-size':
        selected['files']['definitions/factual-source.json']['size'] = True
    elif defect == 'resource-hash':
        selected['files']['definitions/factual-source.json']['sha256'] = 'unversioned'
    elif defect == 'count-type':
        selected['datasets']['factual-v1']['count'] = float(selected['datasets']['factual-v1']['count'])
    elif defect == 'datasets-type':
        selected['datasets'] = None
    else:
        selected[defect] = 'latest'
    with pytest.raises(ValueError, match='External'):
        replace(loaded, external_sources=context).build(tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()
