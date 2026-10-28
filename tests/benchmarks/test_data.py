"""Frozen downloads remain usable offline and fail closed on corrupted inputs."""
import hashlib
import io
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


def test_default_download_command_prepares_all_registered_datasets(monkeypatch):
    calls = []
    monkeypatch.setattr("sys.argv", ["dataset-download"])
    monkeypatch.setattr(data, "snapshot", lambda track: calls.append(track))
    data.main()
    assert calls == ["webfetch", "websearch", "simpleqa-verified", "webmainbench"]
