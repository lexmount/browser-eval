"""Edition selection verifies inputs before changing any current binding."""
import hashlib
import io
import json
import tarfile

import pytest

from harness.benchmarks import pinning


def fixture(root, monkeypatch, defect=None):
    raw_dataset = b'{"id":"sample","cases":[{"id":"task"}]}'
    registry = {'datasets': {'sample': {'path': 'datasets/sample.json', 'count': 1,
                 'sha256': hashlib.sha256(raw_dataset).hexdigest()}}}
    files = {'definitions/registry.json': json.dumps(registry).encode(),
             'definitions/datasets/sample.json': raw_dataset}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, content in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    raw = stream.getvalue()
    manifest = {'schema': 'browser-eval-dataset-release/1', 'track': 'websearch',
                'source_revision': 'b' * 40, 'datasets': registry['datasets'],
                'archive': {'path': 'definitions.tar.gz', 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
                'files': {name: {'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                          for name, value in files.items()}}
    before = {'repository': 'Lexmount/lexbench-websearch', 'revision': '0' * 40,
              'archive': {'sha256': '0' * 64}, 'files': {}}
    lock = root / 'harness/benchmarks/data-lock.json'
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({'datasets': {'websearch': before}}))
    imported = root / 'evaluation/lexbench-websearch.json'
    imported.parent.mkdir()
    imported.write_text(json.dumps({'repository': 'old', 'revision': 'old', 'datasets': {},
                                   'definition_files': {'registry.json': 'old'}}))
    if defect == 'archive': manifest['archive']['sha256'] = '0' * 64
    if defect == 'file': manifest['files']['definitions/registry.json']['sha256'] = '0' * 64
    if defect == 'identity': manifest['track'] = 'webfetch'
    def fetch(url, timeout):
        assert '/resolve/' + 'a' * 40 + '/' in url
        return io.BytesIO(json.dumps(manifest).encode() if url.endswith('manifest.json') else raw)
    monkeypatch.setattr(pinning, 'urlopen', fetch)
    return lock, imported


def test_pin_updates_current_bindings_and_retains_prior_snapshot(tmp_path, monkeypatch):
    lock, imported = fixture(tmp_path, monkeypatch)
    pinning.pin(tmp_path, 'websearch', 'a' * 40)
    selected = json.loads(lock.read_text())['datasets']['websearch']
    assert selected['revision'] == 'a' * 40
    assert selected['history'][0]['revision'] == '0' * 40
    assert json.loads(imported.read_text())['datasets']['sample']['count'] == 1
    assert pinning.validate_binding(tmp_path, 'lexbench-websearch') == 'a' * 40
    assert json.loads(imported.read_text())['repository'] == 'https://huggingface.co/datasets/Lexmount/lexbench-websearch'


@pytest.mark.parametrize('defect', ['archive', 'file', 'identity'])
def test_rejected_release_leaves_all_files_unchanged(tmp_path, monkeypatch, defect):
    paths = fixture(tmp_path, monkeypatch, defect)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError):
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize('revision', ['main', 'latest', 'abcd'])
def test_pin_rejects_floating_or_abbreviated_identity(tmp_path, revision):
    with pytest.raises(ValueError, match='full lowercase'):
        pinning.pin(tmp_path, 'websearch', revision)
