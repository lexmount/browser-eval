"""Locked qualified WCXB inputs and independent official-metric golden cases."""
import gzip
import hashlib
import json
import sys

import pytest

from harness.benchmarks import data
from harness.web_fetch import wcxb_scoring
from harness.web_fetch.wcxb_dataset import validate_manifest
from harness.web_fetch.webmainbench_runner import load_wcxb_source, selected_wcxb_input


@pytest.mark.parametrize('answer,reference,expected', [
    ('Alpha, BETA!', 'alpha beta', (1, 1, 1)),
    ('alpha alpha beta', 'alpha beta beta beta', (2 / 3, 1 / 2, 4 / 7)),
    ('a 1 1', 'A 1', (2 / 3, 1, 4 / 5)),
    ('Straße', 'STRASSE', (0, 0, 0)),
    ('alpha_beta', 'alpha beta', (0, 0, 0)),
    ('你好，世界', '你好 世界', (1, 1, 1)),
    ('', '', (1, 1, 1)),
    ('!!!', '', (1, 1, 1)),
    ('extra', '', (0, 0, 0)),
    ('', 'required', (0, 0, 0)),
])
def test_official_multiset_word_metric_semantics(answer, reference, expected):
    assert wcxb_scoring.word_f1(answer, reference) == pytest.approx(expected)


@pytest.mark.parametrize('text,snippets,expected', [
    ('Cartography', ['art'], 1),
    ('Alpha Beta', ['ALPHA', 'beta', 'absent'], 2 / 3),
    ('Straße', ['STRASSE'], 0),
    ('anything', [], 1),
    ('', ['required'], 0),
])
def test_official_literal_snippet_fraction(text, snippets, expected):
    assert wcxb_scoring.snippet_check(text, snippets) == expected


@pytest.fixture
def edition(tmp_path):
    definitions = tmp_path / 'definitions'
    source = definitions / 'source'
    cases = []
    inventory = hashlib.sha256()
    for identity in ['001', '002']:
        html = f'<html><body>Source paragraph {identity}</body></html>'
        ground_truth = {'url': f'https://example.test/{identity}', 'file_id': identity,
                        'ground_truth': {'main_content': f'Source paragraph {identity}',
                                         'with': ['Source paragraph'], 'without': ['Navigation']},
                        '_internal': {'page_type': {'primary': 'article'}}}
        for relative, raw in [
            (f'test/ground-truth/{identity}.json', json.dumps(ground_truth).encode()),
            (f'test/html/{identity}.html.gz', gzip.compress(html.encode(), mtime=0)),
        ]:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            inventory.update(relative.encode() + b'\0')
            inventory.update(hashlib.sha256(raw).digest())
        cases.append({'file_id': identity, 'page_type': 'article', 'url': ground_truth['url'],
                      'reference': ground_truth['ground_truth']['main_content'],
                      'with': ['Source paragraph'], 'without': ['Navigation']})
    manifest = {'schema_version': 2, 'dataset_id': 'synthetic-qualified', 'source_count': 2,
                'source_sha256': inventory.hexdigest(), 'scope': 'qualified_test', 'split': 'test',
                'size': 2, 'primary_quality_case_count': 2,
                'distribution': {'parent': {'article': 2}, 'sample': {'article': 2}}, 'cases': cases}
    (definitions / 'tasks.json').write_text(json.dumps(manifest))
    return source, definitions / 'tasks.json'


def test_default_qualified_edition_is_locked_and_needs_no_checkout(edition, monkeypatch):
    source, manifest = edition
    monkeypatch.setattr(data, 'definition_path', lambda track, relative: (
        manifest.parent / relative if track == 'wcxb' else pytest.fail('Wrong dataset')))
    assert selected_wcxb_input() == edition
    records, digest = load_wcxb_source(source)
    selected, _ = validate_manifest(records, digest, json.loads(manifest.read_text()))
    assert [row['track_id'] for row in selected] == ['001', '002']
    assert selected[0]['ground_truth']['with'] == ['Source paragraph']
    assert not (source / 'evaluate.py').exists()


@pytest.mark.parametrize('changed', ['html', 'ground-truth', 'count', 'identity', 'reference'])
def test_qualified_edition_rejects_changed_bytes_or_declared_population(edition, changed):
    source, manifest = edition
    if changed in {'html', 'ground-truth'}:
        suffix = 'html.gz' if changed == 'html' else 'json'
        path = source / 'test' / changed / f'001.{suffix}'
        path.write_bytes(path.read_bytes() + b' ')
    else:
        task = json.loads(manifest.read_text())
        if changed == 'count':
            task['source_count'] = 511
        elif changed == 'identity':
            task['cases'][0]['file_id'] = 'foreign'
        else:
            task['cases'][0]['reference'] = 'Wrong independent reference'
        manifest.write_text(json.dumps(task))
    with pytest.raises((ValueError, EOFError, gzip.BadGzipFile)):
        records, digest = load_wcxb_source(source)
        validate_manifest(records, digest, json.loads(manifest.read_text()))


def test_score_command_resolves_qualified_hf_inputs_by_default(edition, tmp_path, monkeypatch):
    source, manifest = edition
    (tmp_path / 'profile.json').write_text('{}')
    monkeypatch.setattr(data, 'definition_path', lambda track, relative: manifest.parent / relative)
    called = {}
    monkeypatch.setattr(wcxb_scoring, 'score', lambda **args: called.update(args))
    monkeypatch.setattr(sys, 'argv', ['score_wcxb_results.py', '--evidence-root', str(tmp_path / 'evidence'),
                                    '--profile', str(tmp_path / 'profile.json')])
    wcxb_scoring.main()
    assert called['source'] == source
    assert called['sample_manifest'] == manifest
