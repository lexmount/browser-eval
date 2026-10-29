"""Generated WCXB selections retain the source edition consumed by runners."""
import json
import sys

import pytest

from harness.benchmarks import data
from harness.web_fetch import wcxb_dataset, webmainbench_runner as runner
from tests.test_wcxb_selected_edition import ReachedCollection, corpus, preflight


@pytest.mark.parametrize('scope,expected', [('qualified', 504), ('full', 504), ('subset', 200)])
@pytest.mark.parametrize('source_option', [False, True])
def test_generate_then_consume_qualified_edition(tmp_path, monkeypatch, scope, expected, source_option):
    source, edition_path, edition = corpus(tmp_path, 504)
    if source_option:
        (source.parent / 'tasks.json').write_text(edition_path.read_text())
    monkeypatch.setattr(data, 'definition_path', lambda track, relative: source if relative == 'source' else edition_path)
    output = tmp_path / 'selection.json'
    arguments = ['select_wcxb_sample.py', '--output', str(output)]
    if source_option:
        arguments += ['--source', str(source)]
    if scope != 'subset':
        arguments.append('--qualified-test' if scope == 'qualified' else '--full-test')
    monkeypatch.setattr(sys, 'argv', arguments)
    wcxb_dataset.main()
    selected_manifest = json.loads(output.read_text())
    assert selected_manifest['schema_version'] == 2
    assert selected_manifest['source_count'] == 504
    assert selected_manifest['dataset_id'] == edition['dataset_id']
    assert 'source_commit' not in selected_manifest
    assert selected_manifest['source_sha256'] == edition['source_sha256']
    assert selected_manifest['size'] == len(selected_manifest['cases']) == expected
    original_cases = {row['file_id']: row for row in edition['cases']}
    assert selected_manifest['cases'] == [original_cases[row['file_id']] for row in selected_manifest['cases']]
    records, digest = runner.load_wcxb_source(source, manifest=output)
    selected, _ = wcxb_dataset.validate_manifest(records, digest, selected_manifest)
    assert len(records) == 504 and len(selected) == expected
    args, called = preflight(tmp_path, monkeypatch, source, output)
    with pytest.raises(ReachedCollection):
        runner.run(args)
    assert called == [True]
    assert not args.output.exists()


@pytest.mark.parametrize('split,count', [('test', 511), ('train', 1497)])
@pytest.mark.parametrize('versioned', [False, True])
def test_discovered_and_explicit_historical_manifests_select_identical_population(tmp_path, split, count, versioned):
    source, selected, manifest = corpus(tmp_path, count, split=split, historical=True)
    if not versioned:
        manifest.pop('schema_version')
        selected.write_text(json.dumps(manifest))
    (source.parent / 'tasks.json').write_text(selected.read_text())
    implicit_records, implicit_hash = runner.load_wcxb_source(source, split)
    explicit_records, explicit_hash = runner.load_wcxb_source(source, split, manifest=selected)
    assert len(implicit_records) == len(explicit_records) == count
    assert implicit_records == explicit_records and implicit_hash == explicit_hash
    (source / split / 'ground-truth/0000.json').unlink()
    for selection in ({}, {'manifest': selected}):
        with pytest.raises(ValueError, match=f'Expected {count}'):
            runner.load_wcxb_source(source, split, **selection)


def test_historical_generator_keeps_original_schema_and_qualified_selection(tmp_path, monkeypatch):
    source, _, _ = corpus(tmp_path, 511, historical=True)
    output = tmp_path / 'historical.json'
    monkeypatch.setattr(sys, 'argv', ['select_wcxb_sample.py', '--source', str(source),
                                     '--qualified-test', '--output', str(output)])
    wcxb_dataset.main()
    manifest = json.loads(output.read_text())
    assert manifest['schema_version'] == 1 and manifest['size'] == 504
    records, digest = runner.load_wcxb_source(source, manifest=output)
    selected, _ = wcxb_dataset.validate_manifest(records, digest, manifest)
    assert len(records) == 511 and len(selected) == 504


@pytest.mark.parametrize('changed', ['source_count', 'hash', 'scope', 'identity', 'reference'])
def test_generated_subset_does_not_weaken_edition_or_selection_checks(tmp_path, monkeypatch, changed):
    source, edition_path, _ = corpus(tmp_path, 504)
    (source.parent / 'tasks.json').write_text(edition_path.read_text())
    output = tmp_path / 'subset.json'
    monkeypatch.setattr(sys, 'argv', ['select_wcxb_sample.py', '--source', str(source), '--output', str(output)])
    wcxb_dataset.main()
    manifest = json.loads(output.read_text())
    if changed == 'source_count':
        manifest['source_count'] = 200
    elif changed == 'hash':
        manifest['source_sha256'] = '0' * 64
    elif changed == 'scope':
        manifest['scope'] = 'qualified_test'
    elif changed == 'identity':
        manifest['cases'][0]['file_id'] = 'foreign'
    else:
        manifest['cases'][0]['reference'] = 'Foreign reference'
    output.write_text(json.dumps(manifest))
    args, called = preflight(tmp_path, monkeypatch, source, output)
    with pytest.raises(ValueError):
        runner.run(args)
    assert called == []


def test_generator_rejects_stale_task_fields_in_selected_qualified_edition(tmp_path, monkeypatch):
    source, edition_path, edition = corpus(tmp_path, 504)
    edition['cases'][0]['reference'] = 'Foreign reference'
    edition_path.write_text(json.dumps(edition))
    monkeypatch.setattr(data, 'definition_path', lambda track, relative: source if relative == 'source' else edition_path)
    output = tmp_path / 'selection.json'
    monkeypatch.setattr(sys, 'argv', ['select_wcxb_sample.py', '--qualified-test', '--output', str(output)])
    with pytest.raises(ValueError, match='task fields differ'):
        wcxb_dataset.main()
    assert not output.exists()
