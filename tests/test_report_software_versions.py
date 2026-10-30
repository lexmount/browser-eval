"""Readers can identify a report build independently of its benchmark edition."""

import json
import pytest
from bs4 import BeautifulSoup

from harness.reporting import versions
from harness.web_fetch import __version__
from harness.web_fetch.published_snapshot import build_snapshot_audit, check_snapshot_audit
from tests.test_benchmark_release_binding import configured


def test_build_records_software_without_relabeling_saved_benchmark(tmp_path, monkeypatch):
    source, profile, _ = configured(tmp_path)
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob('*') if path.is_file()}
    first = tmp_path / 'first'
    build_snapshot_audit(source, first, profile=profile)
    identity = json.loads((first / versions.FILENAME).read_text())
    assert identity['version'] == __version__
    assert identity['role'] == 'report_builder'
    assert len(identity['revision']) == 40
    assert len(identity['source_sha256']) == 64
    page = BeautifulSoup((first / 'index.html').read_text(), 'html.parser')
    assert page.select_one('[data-browser-eval-release]').get_text() == 'Browser Eval v' + __version__
    assert page.select_one('[data-benchmark-release]').get_text() == 'bench v1.0.0'

    # A newer builder must not change the selected tasks, scorer or saved calls.
    monkeypatch.setattr(versions, 'software_identity', lambda: {**identity, 'version': '0.3.0'})
    second = tmp_path / 'second'
    build_snapshot_audit(source, second, profile=profile)
    assert 'Browser Eval v0.3.0' in (second / 'index.html').read_text()
    for file in ('benchmark-release.json', 'results.jsonl', 'dataset.json'):
        assert (first / file).read_bytes() == (second / file).read_bytes()
    assert before == {path.relative_to(source): path.read_bytes()
                      for path in source.rglob('*') if path.is_file()}
    check_snapshot_audit(first)
    check_snapshot_audit(second)


@pytest.mark.parametrize('defect', ['file', 'version', 'commit', 'binding', 'label', 'href'])
def test_release_gate_rejects_missing_or_conflicting_build_identity(tmp_path, defect):
    source, profile, _ = configured(tmp_path)
    output = tmp_path / 'report'
    build_snapshot_audit(source, output, profile=profile)
    path = output / versions.FILENAME
    if defect == 'file':
        path.unlink()
    elif defect in {'version', 'commit'}:
        identity = json.loads(path.read_text())
        identity['version' if defect == 'version' else 'revision'] = 'wrong'
        path.write_text(json.dumps(identity))
    elif defect == 'binding':
        path = output / 'report-manifest.json'
        manifest = json.loads(path.read_text())
        del manifest['browser_eval']
        path.write_text(json.dumps(manifest))
    else:
        path = output / 'index.html'
        content = path.read_text()
        content = content.replace('data-browser-eval-release=', 'data-lost=') if defect == 'label' else content.replace(
            'href="browser-eval-version.json"', 'href="missing-version.json"')
        path.write_text(content)
    with pytest.raises(ValueError, match='Browser Eval'):
        check_snapshot_audit(output)


@pytest.mark.parametrize('bases', [(None, None), ('./', '../'), ('./?preview=token', '../?preview=token')])
def test_shared_identity_handles_both_languages_and_repeat_builds(tmp_path, bases):
    (tmp_path / 'en').mkdir()
    for path, base in zip((tmp_path / 'index.html', tmp_path / 'en/index.html'), bases):
        path.write_text('<html><head>' + (f'<base href="{base}">' if base else '')
                       + '</head><body><main>Results</main><footer></footer></body></html>')
    versions.bind_build_version(tmp_path)
    binding = versions.bind_build_version(tmp_path)
    versions.check_build_version(tmp_path, {'browser_eval': binding})
    for route in ('index.html', 'en/index.html'):
        page = BeautifulSoup((tmp_path / route).read_text(), 'html.parser')
        assert len(page.select('[data-browser-eval-release]')) == 1
        from urllib.parse import urljoin
        base = page.select_one('base')
        document = (tmp_path / route).as_uri()
        target = urljoin(urljoin(document, base['href']) if base else document,
                         page.select_one('[data-browser-eval-release]')['href'])
        assert target == (tmp_path / versions.FILENAME).as_uri()


def test_installed_package_does_not_borrow_an_unrelated_checkout(tmp_path, monkeypatch):
    package = tmp_path / 'installed/harness/reporting'
    package.mkdir(parents=True)
    module = package / 'versions.py'
    module.write_text('installed package source')
    monkeypatch.setattr(versions, '__file__', str(module))
    # This must work without Git and without a repository around the package.
    monkeypatch.setattr(versions.subprocess, 'check_output', lambda *a, **kw: pytest.fail('unrelated Git lookup'))
    identity = versions.software_identity.__wrapped__()
    assert identity['revision'] is None
    assert not identity['modified']
    assert identity['version'] == __version__
    assert len(identity['source_sha256']) == 64


@pytest.mark.parametrize('asset', [
    'web_fetch/assets/report-loader.js',
    'lexbench/assets/render_contract.cjs',
    'web_fetch/html_to_markdown.mjs',
    'web_fetch/workers/openclaw_webfetch_worker.mts',
    'web_fetch/workers/native_dns_darwin.c',
    'web_fetch/report-translations.json',
    'browser_use/report-translations.json',
    'trajectory_replay/messages.json',
    'environments/webarena/runtime/php-fpm-qemu.ini',
    'web_fetch/templates/interpretation-headless.css',
])
def test_installed_runtime_asset_changes_identify_a_different_build(tmp_path, monkeypatch, asset):
    from pathlib import Path

    source = Path(versions.__file__).resolve().parents[1]
    package = tmp_path / 'installed/harness'
    module = package / 'reporting/versions.py'
    module.parent.mkdir(parents=True)
    module.write_bytes(Path(versions.__file__).read_bytes())
    target = package / asset
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((source / asset).read_bytes())
    monkeypatch.setattr(versions, '__file__', str(module))
    original = versions.software_identity.__wrapped__()
    assert original['revision'] is None
    target.write_bytes(target.read_bytes() + b'\nchanged runtime asset\n')
    changed = versions.software_identity.__wrapped__()
    assert changed['source_sha256'] != original['source_sha256']
    assert {k: v for k, v in changed.items() if k != 'source_sha256'} == {
        k: v for k, v in original.items() if k != 'source_sha256'}
    target.unlink()
    assert versions.software_identity.__wrapped__()['source_sha256'] not in {
        original['source_sha256'], changed['source_sha256']}


def test_generated_caches_do_not_change_installed_build_identity(tmp_path, monkeypatch):
    module = tmp_path / 'harness/reporting/versions.py'
    module.parent.mkdir(parents=True)
    module.write_text('runtime source')
    monkeypatch.setattr(versions, '__file__', str(module))
    original = versions.software_identity.__wrapped__()
    cache = module.parent / '__pycache__'
    cache.mkdir()
    (cache / 'versions.pyc').write_bytes(b'compiled cache')
    (cache / 'metadata.json').write_text('{}')
    (module.parent / 'versions.pyc').write_bytes(b'compiled cache')
    assert versions.software_identity.__wrapped__() == original


@pytest.mark.parametrize('scorer', ['lexbench_webfetch', 'lexbench_websearch'])
def test_installed_scorer_sources_are_part_of_the_build_identity(tmp_path, monkeypatch, scorer):
    module = tmp_path/'installed/harness/reporting/versions.py'
    module.parent.mkdir(parents=True)
    module.write_text('report builder')
    target = tmp_path/'installed'/scorer/'scoring.py'
    target.parent.mkdir()
    target.write_text('score = 1')
    unrelated = tmp_path/'unrelated-checkout'
    (unrelated/'.git').mkdir(parents=True)
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr(versions, '__file__', str(module))
    original = versions.software_identity.__wrapped__()
    target.write_text('score = 2')
    changed = versions.software_identity.__wrapped__()
    assert changed['source_sha256'] != original['source_sha256']
    assert changed['revision'] is None
    assert not changed['modified']
    target.unlink()
    assert versions.software_identity.__wrapped__()['source_sha256'] not in {
        original['source_sha256'], changed['source_sha256']}


@pytest.mark.parametrize('scorer', ['lexbench_webfetch', 'lexbench_websearch'])
@pytest.mark.parametrize('untracked', [False, True])
def test_checkout_scorer_edits_mark_the_builder_modified(tmp_path, monkeypatch, scorer, untracked):
    import subprocess
    module = tmp_path/'checkout/src/harness/reporting/versions.py'
    module.parent.mkdir(parents=True)
    module.write_text('report builder')
    root = module.parents[3]
    (root/'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1.0.0"\n')
    target = root/'src'/scorer/'scoring.py'
    target.parent.mkdir()
    target.write_text('score = 1')
    for args in [('init', '-q'), ('add', '.'),
                 ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                  'commit', '-qm', 'Initial sources')]:
        subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(versions, '__file__', str(module))
    original = versions.software_identity.__wrapped__()
    assert not original['modified']
    if untracked:
        target = target.with_name('new_rule.py')
    target.write_text('score = 2')
    changed = versions.software_identity.__wrapped__()
    assert changed['modified']
    assert changed['source_sha256'] != original['source_sha256']
    assert changed['revision'] == original['revision']
