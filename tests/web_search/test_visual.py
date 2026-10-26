"""The release proof records actual local rendering, never a readability assertion."""
import json
import os
from copy import deepcopy

import pytest

from harness.web_search import visual


def report_fixture(root):
    root.mkdir()
    (root / 'manifest.json').write_text(json.dumps({
        'primary_language': 'zh', 'languages': ['zh'],
        'authored': {'presentation': 'comparison-report'},
    }))
    for filename in ('index', 'dataset', 'results', 'performance',
                     'research', 'capabilities', 'quality'):
        (root / (filename + '.html')).write_text(
            '<!doctype html><html lang="zh"><meta charset="utf-8"><main><h1>' + filename + '</h1></main>')


@pytest.mark.parametrize('primary', ['zh', 'en'])
def test_capture_produces_complete_bound_proof_and_rejects_tampering(tmp_path, monkeypatch, primary):
    binary = os.environ.get('BROWSER_EVAL_TEST_BROWSER_BINARY')
    if not binary:
        try:
            binary = visual.browser_executable()
        except ValueError:
            pytest.skip('Install Chromium or set BROWSER_EVAL_TEST_BROWSER_BINARY')
    monkeypatch.setenv('BROWSER_USE_VISUAL_BROWSER', binary)
    root, receipt = tmp_path / 'report', tmp_path / 'reader.json'
    report_fixture(root)
    if primary == 'en':
        manifest = json.loads((root / 'manifest.json').read_text())
        manifest.update(primary_language='en', languages=['en', 'zh'])
        (root / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'en').mkdir()
        for page in root.glob('*.html'):
            (root / 'en' / page.name).write_text(page.read_text().replace('lang="zh"', 'lang="en"'))
    visual.capture(root, receipt)
    assert visual.verify(root, receipt)
    original = json.loads(receipt.read_text())
    assert len(original['captures']) == (14 if primary == 'en' else 7)
    assert next(item['route'] for item in original['captures'] if item['language'] == primary and item['route_class'] == 'home') == ('en/index.html' if primary == 'en' else 'index.html')
    assert {capture['route_class'] for capture in original['captures']} >= {'quality', 'performance'}
    assert original['browser_version']
    assert set(original['producer_sources']) == {'harness/web_search/visual.py', 'harness/reporting/visual.py', 'harness/web_search/pages.py'}
    assert all('readable' not in capture for capture in original['captures'])
    for damage in ('schema', 'manifest', 'producer', 'search-producer', 'shared-producer', 'browser', 'version', 'duplicate',
                   'page-language', 'final-route', 'missing', 'overflow', 'tiny', 'page-error', 'resource', 'heading', 'image', 'path'):
        proof = deepcopy(original)
        first = proof['captures'][0]
        if damage == 'schema': proof['schema'] = 'legacy'
        elif damage == 'manifest': proof['manifest_sha256'] = '0' * 64
        elif damage == 'producer': proof['producer_sources'] = {}
        elif damage == 'search-producer': proof['producer_sources']['harness/web_search/visual.py'] = '0' * 64
        elif damage == 'shared-producer': proof['producer_sources']['harness/reporting/visual.py'] = '0' * 64
        elif damage == 'browser': proof['browser_sha256'] = '0' * 64
        elif damage == 'version': proof['browser_version'] = ''
        elif damage == 'duplicate': proof['captures'][-1] = deepcopy(first)
        elif damage == 'page-language': first['page_language'] = 'en' if first['language'] == 'zh' else 'zh'
        elif damage == 'final-route': first['final_path'] = 'wrong.html'
        elif damage == 'missing': proof['captures'].pop()
        elif damage == 'overflow': first['overflow'] = 20
        elif damage == 'tiny': first['tiny'] = ['H1']
        elif damage == 'page-error': first['page_errors'] = ['ReferenceError']
        elif damage == 'resource': first['failed_resources'] = ['404 app.js']
        elif damage == 'heading': first['heading_count'] = 0
        elif damage == 'image': first['sha256'] = '0' * 64
        elif damage == 'path': first['path'] = '../outside.png'
        receipt.write_text(json.dumps(proof))
        with pytest.raises((ValueError, OSError)):
            visual.verify(root, receipt)
    receipt.write_text(json.dumps(original))
    assert visual.verify(root, receipt)


def test_legacy_asserted_readability_is_not_accepted(tmp_path):
    root, receipt = tmp_path / 'report', tmp_path / 'reader.json'
    report_fixture(root)
    receipt.write_text(json.dumps({'captures': [{'readable': True}]}))
    with pytest.raises(ValueError, match='producer proof'):
        visual.verify(root, receipt)


@pytest.mark.parametrize('primary,languages', [('zh', ['zh']), ('zh', ['zh', 'en']), ('en', ['en', 'zh'])])
def test_standalone_routes_follow_saved_primary_language(tmp_path, primary, languages):
    root = tmp_path / 'report'
    report_fixture(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest.update(primary_language=primary, languages=languages)
    (root / 'manifest.json').write_text(json.dumps(manifest))
    secondary = 'en' if 'en' in languages else None
    if secondary:
        (root / secondary).mkdir()
        for page in root.glob('*.html'):
            (root / secondary / page.name).write_bytes(page.read_bytes())
    routes = visual.routes(root)
    assert len(routes) == 7 * len(languages)
    assert {lang for lang, _, _ in routes} == set(languages)
    assert next(route for lang, chapter, route in routes if lang == primary and chapter == 'home') == ('en/index.html' if primary == 'en' else 'index.html')
    if secondary:
        assert next(route for lang, chapter, route in routes if lang == secondary and chapter == 'home') == 'en/index.html'
    (root / 'quality.html').unlink()
    with pytest.raises(ValueError, match='missing'):
        visual.routes(root)
