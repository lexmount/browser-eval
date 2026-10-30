"""Frozen downloads remain usable offline and fail closed on corrupted inputs."""
import hashlib
import io
import json
import tarfile

import pytest

from harness.benchmarks import data


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as out:
        for name, content, kind in entries:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            if kind == "symlink":
                item.type, item.linkname, item.size = tarfile.SYMTYPE, "/outside", 0
            out.addfile(item, io.BytesIO(content))
    return stream.getvalue()


@pytest.fixture
def download(tmp_path, monkeypatch):
    name, content = "definitions/tasks.json", b'{"cases":[{"id":"a"}]}'
    raw = archive([(name, content, "file")])
    selected = {"revision": "a" * 40, "repository": "Lexmount/test",
                "archive": {"path": "definitions.tar.gz", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)},
                "files": {name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}}}
    monkeypatch.setattr(data, "binding", lambda track, revision=None: selected)
    monkeypatch.setenv("BROWSER_EVAL_DATASET_CACHE", str(tmp_path / "data"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return io.BytesIO(raw)
    monkeypatch.setattr(data, "urlopen", fetch)
    data._resolve.cache_clear()
    yield selected, raw, calls
    data._resolve.cache_clear()


def test_pinned_download_reopens_offline_without_a_token(download, monkeypatch):
    selected, raw, calls = download
    path = data.definition_path("webfetch", "tasks.json")
    assert path.read_bytes() == b'{"cases":[{"id":"a"}]}'
    assert calls == ["https://huggingface.co/datasets/Lexmount/test/resolve/" + "a" * 40 + "/definitions.tar.gz"]
    data._resolve.cache_clear()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert data.definition_path("webfetch", "tasks.json") == path
    assert len(calls) == 1


def test_corrupted_cache_is_rejected_even_with_a_warm_process(download):
    path = data.definition_path("webfetch", "tasks.json")
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="hash or size"):
        data.definition_path("webfetch", "tasks.json")


def test_directory_lookup_verifies_descendants_and_preserves_original_api(download):
    root = data.definition_path("webfetch", ".")
    assert (root / "tasks.json").is_file()
    (root / "tasks.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash or size"):
        data.definition_path("webfetch", ".")


def test_offline_cache_miss_has_no_network_fallback(download, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(FileNotFoundError, match="not cached"):
        data.snapshot("webfetch")
    assert download[2] == []


def test_extra_cached_definition_cannot_enter_the_next_run(download):
    root = data.snapshot("webfetch")
    (root / "definitions/unbound.json").write_text('{}')
    data._resolve.cache_clear()
    with pytest.raises(ValueError, match="inventory differs"):
        data.snapshot("webfetch")


def test_explicit_repair_preserves_invalid_cache_and_restores_locked_bytes(download, monkeypatch):
    path = data.definition_path('webfetch', 'tasks.json')
    root = path.parent.parent
    (root / '.DS_Store').write_bytes(b'finder metadata')
    data._resolve.cache_clear()
    with pytest.raises(ValueError, match=r'extra=.*DS_Store.*--repair'):
        data.snapshot('webfetch')
    monkeypatch.setattr('sys.argv', ['datasets', '--repair', 'webfetch'])
    data.main()
    assert data.definition_path('webfetch', 'tasks.json').read_bytes() == b'{"cases":[{"id":"a"}]}'
    assert not (root / '.DS_Store').exists()
    retained = list(root.parent.glob(root.name + '.invalid-*'))
    assert len(retained) == 1
    assert (retained[0] / '.DS_Store').read_bytes() == b'finder metadata'
    assert len(download[2]) == 2


def test_offline_repair_leaves_original_cache_intact(download, monkeypatch):
    path = data.definition_path('webfetch', 'tasks.json')
    extra = path.parent.parent / '.DS_Store'
    extra.write_bytes(b'preserve me')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    with pytest.raises(ValueError, match='requires online access'):
        data.repair('webfetch')
    assert extra.read_bytes() == b'preserve me'
    assert len(download[2]) == 1


def test_failed_repair_download_retains_original_snapshot(download, monkeypatch):
    path = data.definition_path('webfetch', 'tasks.json')
    root = path.parent.parent
    (root / 'extra.json').write_bytes(b'old')
    def unavailable(*args, **kwargs):
        raise OSError('network unavailable')
    monkeypatch.setattr(data, 'urlopen', unavailable)
    with pytest.raises(OSError, match='network unavailable'):
        data.repair('webfetch')
    retained = list(root.parent.glob(root.name + '.invalid-*'))
    assert (retained[0] / 'extra.json').read_bytes() == b'old'
    assert (retained[0] / 'definitions/tasks.json').read_bytes() == b'{"cases":[{"id":"a"}]}'


def test_directory_lookup_resolves_snapshot_once(tmp_path, monkeypatch):
    files = {}
    for index in range(50):
        relative = f'definitions/source/{index}.html'
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(str(index).encode())
        files[relative] = {'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(data, 'binding', lambda *args: {'revision': 'a' * 40, 'files': files})
    calls = []
    def resolve(*args):
        calls.append(args)
        return tmp_path
    monkeypatch.setattr(data, '_resolve', resolve)
    assert len(list(data.definition_path('wcxb', 'source').iterdir())) == 50
    assert len(calls) == 1


def test_lock_cache_is_defensive_and_invalidates_after_atomic_update(tmp_path, monkeypatch):
    module = tmp_path / 'data.py'
    lock = tmp_path / 'data-lock.json'
    current = {'repository': 'Lexmount/test', 'revision': 'a' * 40, 'files': {'x': {'sha256': 'original'}}}
    lock.write_text(json.dumps({'datasets': {'webfetch': current}}))
    monkeypatch.setattr(data, '__file__', str(module))
    original_read = type(lock).read_bytes
    reads = []
    def read(path):
        if path == lock:
            reads.append(path)
        return original_read(path)
    monkeypatch.setattr(type(lock), 'read_bytes', read)
    first = data.binding('webfetch')
    first['files']['x']['sha256'] = 'changed by caller'
    assert data.binding('webfetch')['files']['x']['sha256'] == 'original'
    assert len(reads) == 1
    current['revision'] = 'b' * 40
    replacement = tmp_path / 'replacement.json'
    replacement.write_text(json.dumps({'datasets': {'webfetch': current}}))
    replacement.replace(lock)
    assert data.binding('webfetch')['revision'] == 'b' * 40
    assert len(reads) == 2


def test_downloaded_bytes_must_match_the_lock(download):
    download[0]["archive"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="archive hash"):
        data.snapshot("webfetch")


@pytest.mark.parametrize("defect", ["traversal", "link", "duplicate", "missing", "payload"])
def test_archive_cannot_escape_or_change_its_declared_inventory(tmp_path, download, defect):
    selected = download[0]
    entry = ("definitions/tasks.json", b'{"cases":[{"id":"a"}]}', "file")
    entries = [entry]
    if defect == "traversal":
        entries.append(("../outside", b"payload", "file"))
    elif defect == "link":
        entries = [(entry[0], b"", "symlink")]
    elif defect == "duplicate":
        entries.append(entry)
    elif defect == "missing":
        entries = []
    else:
        entries = [(entry[0], b"X" * len(entry[1]), "file")]
    destination = tmp_path / "snapshot"
    destination.mkdir()
    with pytest.raises(ValueError):
        data.unpack(archive(entries), destination, selected)
    assert not (tmp_path / "outside").exists()


def test_floating_or_unrecorded_revisions_cannot_be_selected():
    for revision in ["main", "latest", "a" * 40]:
        with pytest.raises(ValueError, match="not recorded"):
            data.binding("webfetch", revision)


def test_default_download_command_prepares_all_registered_datasets(tmp_path, monkeypatch):
    registered = {
        name: {"repository": "owner/" + name, "revision": "a" * 40}
        for name in ["existing-family", "newly-registered-family"]
    }
    module = tmp_path / "data.py"
    (tmp_path / "data-lock.json").write_text(json.dumps({"datasets": registered}))
    monkeypatch.setattr(data, "__file__", str(module))
    calls = []
    monkeypatch.setattr("sys.argv", ["dataset-download"])
    monkeypatch.setattr(data, "snapshot", lambda track: calls.append(track))
    data.main()
    assert set(calls) == set(registered)
    assert len(calls) == len(registered)


def test_preparing_search_also_prepares_its_factual_source(monkeypatch):
    calls = []
    monkeypatch.setattr("sys.argv", ["dataset-download", "websearch"])
    monkeypatch.setattr(data, "snapshot", lambda track: calls.append(track))
    data.main()
    assert calls == ["websearch", "simpleqa-verified"]


def test_search_preparation_is_sufficient_for_offline_factual_loading(tmp_path, monkeypatch):
    from lexbench_websearch.inputs import load_dataset
    raw_cases = b'{"id":"factual-v1","cases":[{"id":"question","query":"Find the answer"}]}'
    spec = {'path': 'factual.json', 'count': 1, 'sha256': hashlib.sha256(raw_cases).hexdigest()}
    registry = json.dumps({'datasets': {'factual-v1': spec}}).encode()
    contents = {'websearch': {'definitions/registry.json': registry},
                'simpleqa-verified': {'definitions/factual.json': raw_cases}}
    bindings, payloads = {}, {}
    for track, files in contents.items():
        raw = archive([(name, content, 'file') for name, content in files.items()])
        payloads[track] = raw
        bindings[track] = {'repository': 'Lexmount/' + track, 'revision': 'a' * 40,
                          'archive': {'path': 'definitions.tar.gz', 'size': len(raw),
                                      'sha256': hashlib.sha256(raw).hexdigest()},
                          'files': {name: {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                                    for name, content in files.items()}}
    bindings['websearch']['dataset_sources'] = {'factual-v1': 'simpleqa-verified'}
    monkeypatch.setattr(data, 'binding', lambda track, revision=None: bindings[track])
    monkeypatch.setenv('BROWSER_EVAL_DATASET_CACHE', str(tmp_path / 'cache'))
    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    monkeypatch.setattr(data, 'urlopen', lambda url, timeout: io.BytesIO(
        payloads['simpleqa-verified' if '/simpleqa-verified/' in url else 'websearch']))
    monkeypatch.setattr('sys.argv', ['dataset-download', 'websearch'])
    data._resolve.cache_clear()
    try:
        data.main()
        monkeypatch.setenv('HF_HUB_OFFLINE', '1')
        monkeypatch.setattr(data, 'urlopen', lambda *args, **kwargs: pytest.fail('Unexpected offline download'))
        data._resolve.cache_clear()
        assert load_dataset('factual-v1')['cases'][0]['id'] == 'question'
    finally:
        data._resolve.cache_clear()


@pytest.mark.parametrize('mutation', ['same-size-change', 'delete', 'add'])
def test_public_snapshot_revalidates_warm_files_and_inventory(download, mutation):
    root = data.snapshot('webfetch')
    target = root / 'definitions/tasks.json'
    if mutation == 'same-size-change':
        raw = target.read_bytes()
        target.write_bytes(raw.replace(b'"a"', b'"b"'))
        assert target.stat().st_size == len(raw)
    elif mutation == 'delete':
        target.unlink()
    else:
        (root / 'definitions/unlocked.json').write_text('{}')
    # Do not clear resolution caches: public snapshots are verified every time.
    with pytest.raises(ValueError, match='hash or size|inventory differs'):
        data.snapshot('webfetch')
    assert len(download[2]) == 1


def test_warm_single_definition_lookup_does_not_rehash_entire_snapshot(download, monkeypatch):
    root = data.snapshot('webfetch')
    original = data.verify_file
    checked = []
    def verify(path, expected):
        checked.append(path.relative_to(root).as_posix())
        original(path, expected)
    monkeypatch.setattr(data, 'verify_file', verify)
    monkeypatch.setattr(data, '_verify_snapshot', lambda *args: pytest.fail('Single-file lookup walked entire snapshot'))
    assert data.definition_path('webfetch', 'tasks.json') == root / 'definitions/tasks.json'
    assert checked == ['definitions/tasks.json']
