"""Rebuild saved runs against their frozen HF input, even after the lock advances."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.benchmarks import data
from harness.trajectory_replay.report_inputs import replay_suite_for_hash


@pytest.mark.parametrize('public', [False, True])
@pytest.mark.parametrize('mode', ['unrecorded', 'recorded', 'identical', 'explicit'])
def test_rebuild_resolves_historical_suite_before_loading_it(tmp_path, monkeypatch, public, mode):
    track = 'webarena-replay' if public else 'trajectory-replay'
    definition = 'suites/human-qualified-v1.json' if public else 'suites/curated-v1.json'
    original = tmp_path / 'old-suite.json'
    original.write_text('{"edition":"original"}')
    newer = tmp_path / 'current-suite.json'
    newer.write_bytes(original.read_bytes() if mode == 'identical' else b'{"edition":"new"}')
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    old = {'revision': '1'*40, 'files': {'definitions/' + definition: {'sha256': digest}}}
    current = {'revision': '2'*40, 'history': [old], 'files': {
        'definitions/' + definition: {'sha256': hashlib.sha256(newer.read_bytes()).hexdigest()},
    }}
    lookup = MagicMock(side_effect=lambda name, revision=None:
                       old if revision == old['revision'] else current)
    if mode == 'explicit':
        lookup.side_effect = AssertionError('Explicit suite must not consult HF bindings')
    monkeypatch.setattr(data, 'binding', lookup)
    resolve = MagicMock(side_effect=lambda name, relative, revision=None:
                        original if revision == old['revision'] else newer)
    monkeypatch.setattr(data, 'definition_path', resolve)
    manifest = {'suite_sha256': digest}
    if mode in {'recorded', 'identical', 'explicit'}:
        manifest['suite_dataset'] = {'benchmark_dataset': track, 'definition': definition,
                                     'revision': old['revision']}
    artifacts = tmp_path / 'run'
    artifacts.mkdir()
    (artifacts/'attempts.jsonl').write_text('')
    (artifacts/'manifest.json').write_text(json.dumps(manifest))
    root = Path(__file__).resolve().parents[2]
    script = root / 'examples/trajectory_replay'
    if public:
        script /= 'public_webarena'
    spec = importlib.util.spec_from_file_location(
        'saved_replay_rebuild', script/'rebuild_report.py'
    )
    rebuild = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rebuild)

    class ExactInputSelected(Exception):
        pass

    def load(path):
        assert path == original
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest['suite_sha256']
        raise ExactInputSelected

    monkeypatch.setattr(rebuild, 'load_suite', load)
    argv = ['rebuild', '--artifacts', str(artifacts), '--reports', str(tmp_path/'reports')]
    if mode == 'explicit':
        argv += ['--suite', str(original)]
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(ExactInputSelected):
        rebuild.main()
    if mode == 'explicit':
        lookup.assert_not_called()
        resolve.assert_not_called()
    else:
        resolve.assert_called_once_with(track, definition, old['revision'])


def test_resolved_history_bytes_are_verified(tmp_path, monkeypatch):
    expected = hashlib.sha256(b'original').hexdigest()
    selected = {'revision': '1'*40, 'files': {
        'definitions/suites/curated-v1.json': {'sha256': expected},
    }}
    changed = tmp_path/'changed.json'
    changed.write_bytes(b'different')
    monkeypatch.setattr(data, 'binding', lambda name, revision=None: selected)
    monkeypatch.setattr(data, 'definition_path', lambda *args: changed)
    with pytest.raises(ValueError, match='changed'):
        replay_suite_for_hash(expected, family='curated')
