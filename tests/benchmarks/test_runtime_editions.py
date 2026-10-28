"""Runtime defaults use pinned editions while explicit historical inputs remain usable."""
import subprocess

import pytest

from harness.benchmarks import data
from harness.web_fetch import webmainbench_runner as runner
from harness.web_fetch import webmainbench_scoring as scoring


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org',
                                    '-C', str(root), *args], text=True).strip()


def test_default_corpus_and_scorer_come_from_the_same_locked_edition(tmp_path, monkeypatch):
    selected = data.binding('webmainbench')
    item = selected['datasets'][selected['default_dataset']]
    observed = []
    def file(track, relative):
        observed.append((track, relative))
        return tmp_path / 'corpus.jsonl'
    monkeypatch.setattr(data, 'definition_path', file)
    path, sha, commit = runner.selected_input()
    assert path == tmp_path / 'corpus.jsonl'
    assert sha == item['sha256']
    assert commit == selected['scorer']['revision']
    assert observed == [('webmainbench', item['path'])]


def test_historical_file_does_not_require_current_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(data, 'definition_path', lambda *args: pytest.fail('Unexpected current dataset download'))
    path = tmp_path / 'historical.jsonl'
    assert runner.selected_input(path) == (path, runner.DATASET_SHA256, runner.SCORER_COMMIT)
    assert runner.selected_input(path, 'a'*64, 'b'*40) == (path, 'a'*64, 'b'*40)


@pytest.mark.parametrize('damage', [None, 'dirty', 'revision'])
def test_scorer_install_selects_exact_commit_and_rejects_changed_cache(tmp_path, monkeypatch, damage):
    source = tmp_path / 'upstream';source.mkdir()
    git(source, 'init', '-q')
    module = source / 'webmainbench/module.py';module.parent.mkdir();module.write_text('first')
    git(source, 'add', '.');git(source, 'commit', '-qm', 'First')
    revision = git(source, 'rev-parse', 'HEAD')
    module.write_text('second');git(source, 'commit', '-qam', 'Second')
    monkeypatch.setattr(data, 'binding', lambda track: {'scorer': {'repository': str(source), 'revision': revision}})
    monkeypatch.setattr(data, 'cache_home', lambda: tmp_path / 'datasets')
    installed = scoring.prepare_scorer()
    assert git(installed, 'rev-parse', 'HEAD') == revision
    assert (installed / 'webmainbench/module.py').read_text() == 'first'
    if damage:
        (installed / 'webmainbench/module.py').write_text('changed')
        if damage == 'revision':git(installed, 'commit', '-qam', 'Changed')
        with pytest.raises(ValueError, match='clean tracked|commit mismatch'):
            scoring.prepare_scorer()
    else:
        original_run = scoring.subprocess.run
        def local_only(command, *args, **kwargs):
            assert not set(command) & {'clone', 'fetch', 'pull', 'ls-remote'}
            return original_run(command, *args, **kwargs)
        monkeypatch.setattr(scoring.subprocess, 'run', local_only)
        assert scoring.prepare_scorer() == installed


def test_fetch_score_cli_works_offline_with_an_empty_dataset_cache(tmp_path):
    import json
    import os
    import sys
    task = tmp_path / 'task.json'
    task.write_text(json.dumps({'id': 'local', 'url': 'https://example.org',
                               'required_facts': [{'id': 'fact', 'alternatives': ['Complete answer']}]}))
    response = tmp_path / 'response.txt';response.write_text('Complete answer')
    environment = {**os.environ, 'BROWSER_EVAL_DATASET_CACHE': str(tmp_path / 'empty-cache'), 'HF_HUB_OFFLINE': '1'}
    result = subprocess.run([sys.executable, '-m', 'lexbench_webfetch.cli', 'score', '--task', str(task),
                             '--response', str(response)], cwd=tmp_path, env=environment,
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)['passed'] is True
    assert not (tmp_path / 'empty-cache').exists()
