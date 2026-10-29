"""Explicit WCXB source/manifest pairs override unrelated neighboring editions."""
import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest

from harness.web_fetch import webmainbench_runner as runner
from harness.web_fetch.wcxb_dataset import validate_manifest


def corpus(tmp_path, count, *, split='test', historical=False):
    source = tmp_path / 'source'
    inventory = hashlib.sha256()
    cases = []
    for index in range(count):
        identity = f'{index:04d}'
        text = f'Independent reference {identity}' if not historical or index < 504 else ''
        truth = {'url': f'https://example.test/{identity}',
                 'ground_truth': {'main_content': text, 'with': [], 'without': []},
                 '_internal': {'page_type': {'primary': 'article'}}}
        for relative, raw in [(f'{split}/ground-truth/{identity}.json', json.dumps(truth).encode()),
                              (f'{split}/html/{identity}.html.gz', gzip.compress(f'<html><body>{text}</body></html>'.encode(), mtime=0))]:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            inventory.update(relative.encode() + b'\0')
            inventory.update(hashlib.sha256(raw).digest())
        if text:
            cases.append({'file_id': identity, 'page_type': 'article', 'url': truth['url'],
                          'reference': text, 'with': [], 'without': []})
    manifest = {'schema_version': 1 if historical else 2, 'dataset_id': 'synthetic-wcxb',
                'split': split, 'scope': 'qualified_test', 'source_sha256': inventory.hexdigest(),
                'source_count': count, 'size': len(cases), 'primary_quality_case_count': len(cases),
                'distribution': {'parent': {'article': count}, 'sample': {'article': len(cases)}},
                'cases': cases if not historical else [{key: case[key] for key in ('file_id', 'page_type')} for case in cases]}
    selected = tmp_path / 'external/selected.json'
    selected.parent.mkdir()
    selected.write_text(json.dumps(manifest))
    return source, selected, manifest


class ReachedCollection(RuntimeError):
    pass


def preflight(tmp_path, monkeypatch, source, manifest):
    provider = runner.WCXB_PROVIDERS[0]
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'providers': [provider], 'expected_versions': {provider: 'fixture'}}))
    called = []
    def stop_before_providers(*args):
        called.append(True)
        raise ReachedCollection('source validation completed before collection')
    monkeypatch.setattr(runner, 'load_runtime_env', stop_before_providers)
    args = SimpleNamespace(output=tmp_path / 'new-run', benchmark='wcxb', provider=provider,
                           dataset=source, case_ids_file=manifest, profile=profile, limit=None, env_file=None)
    return args, called


@pytest.mark.parametrize('adjacent', [False, True])
def test_runner_uses_external_qualified_504_manifest(tmp_path, monkeypatch, adjacent):
    source, selected, manifest = corpus(tmp_path, 504)
    if adjacent:
        (source.parent / 'tasks.json').write_text('unrelated invalid JSON')
    assert runner.selected_wcxb_input(source, selected) == (source, selected)
    args, called = preflight(tmp_path, monkeypatch, source, selected)
    with pytest.raises(ReachedCollection):
        runner.run(args)
    assert called == [True]
    assert not args.output.exists()


@pytest.mark.parametrize('versioned', [False, True])
def test_explicit_historical_511_source_ignores_qualified_neighbor(tmp_path, monkeypatch, versioned):
    source, selected, manifest = corpus(tmp_path, 511, historical=True)
    if not versioned:
        manifest.pop('schema_version')
        selected.write_text(json.dumps(manifest))
    (source.parent / 'tasks.json').write_text(json.dumps({**manifest, 'schema_version': 2, 'source_count': 504}))
    args, called = preflight(tmp_path, monkeypatch, source, selected)
    with pytest.raises(ReachedCollection):
        runner.run(args)
    assert called == [True]


@pytest.mark.parametrize('changed', ['hash', 'count', 'identity', 'reference', 'split', 'scope'])
def test_external_qualified_manifest_rejects_wrong_population_or_binding(tmp_path, monkeypatch, changed):
    source, selected, manifest = corpus(tmp_path, 504)
    if changed == 'hash':
        manifest['source_sha256'] = '0' * 64
    elif changed == 'count':
        manifest['source_count'] = 511
    elif changed == 'identity':
        manifest['cases'][0]['file_id'] = 'foreign'
    elif changed == 'reference':
        manifest['cases'][0]['reference'] = 'Foreign reference'
    elif changed == 'split':
        manifest['split'] = 'train'
    else:
        manifest['scope'] = 'stratified_sample'
    selected.write_text(json.dumps(manifest))
    args, called = preflight(tmp_path, monkeypatch, source, selected)
    with pytest.raises(ValueError):
        runner.run(args)
    assert called == []
    assert not args.output.exists()


def test_explicit_historical_selection_still_requires_511_test_sources(tmp_path):
    source, selected, _ = corpus(tmp_path, 510, historical=True)
    with pytest.raises(ValueError, match='Expected 511'):
        runner.load_wcxb_source(source, manifest=selected)


def test_explicit_historical_selection_rejects_changed_source_hash(tmp_path, monkeypatch):
    source, selected, manifest = corpus(tmp_path, 511, historical=True)
    manifest['source_sha256'] = '0' * 64
    selected.write_text(json.dumps(manifest))
    args, called = preflight(tmp_path, monkeypatch, source, selected)
    with pytest.raises(ValueError, match='manifest differs'):
        runner.run(args)
    assert called == []


def test_historical_train_count_remains_1497(tmp_path):
    source, selected, _ = corpus(tmp_path, 1497, split='train', historical=True)
    records, _ = runner.load_wcxb_source(source, 'train', manifest=selected)
    assert len(records) == 1497
    (source / 'train/ground-truth/0000.json').unlink()
    with pytest.raises(ValueError, match='Expected 1497'):
        runner.load_wcxb_source(source, 'train', manifest=selected)


def test_default_adjacent_qualified_edition_still_validates(tmp_path):
    source, selected, manifest = corpus(tmp_path, 504)
    (source.parent / 'tasks.json').write_text(selected.read_text())
    records, digest = runner.load_wcxb_source(source)
    accepted, _ = validate_manifest(records, digest, manifest)
    assert len(accepted) == 504


def test_scoring_uses_its_explicit_historical_manifest_despite_conflicting_neighbor(tmp_path):
    from harness.web_fetch.wcxb_scoring import score
    from tests.wcxb_fixture import create_cohort

    options = create_cohort(tmp_path)
    (options['source'].parent / 'tasks.json').write_text('unrelated invalid JSON')
    summary = score(**options)
    assert all(value['cases'] == 2 for value in summary['providers'].values())
    assert len((options['evidence_root'] / 'wcxb-scores.jsonl').read_text().splitlines()) == 6


def test_explicit_manifest_must_be_a_mapping(tmp_path):
    source, selected, _ = corpus(tmp_path, 504)
    selected.write_text('null')
    with pytest.raises(ValueError, match='Invalid WCXB source edition'):
        runner.load_wcxb_source(source, manifest=selected)
