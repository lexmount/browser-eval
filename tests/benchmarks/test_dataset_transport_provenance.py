"""Hub transport and legacy provenance remain consistent across edition changes."""
import copy
import hashlib
import io
import json
import tarfile
from types import SimpleNamespace
from urllib.request import Request

import pytest

from harness.benchmarks import data, pinning
from tests.benchmarks.test_pinning import fixture


def _release(label):
    raw = json.dumps({"id": "sample", "cases": [{"id": "task", "value": label}]}).encode()
    datasets = {"sample": {"path": "datasets/sample.json", "count": 1,
                           "sha256": hashlib.sha256(raw).hexdigest()}}
    files = {"definitions/registry.json": json.dumps({"datasets": datasets}).encode(),
             "definitions/datasets/sample.json": raw}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    raw = stream.getvalue()
    manifest = {"schema": "browser-eval-dataset-release/1", "track": "websearch",
                "source_revision": "b" * 40, "datasets": datasets,
                "files": {name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                          for name, content in files.items()},
                "archive": {"path": "definitions.tar.gz", "size": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest()}}
    return json.dumps(manifest).encode(), raw


def test_repinning_a_previous_edition_cannot_rewrite_first_source_authority(tmp_path, monkeypatch):
    lock, _ = fixture(tmp_path, monkeypatch)
    releases = {"a" * 40: _release("original"), "b" * 40: _release("corrected"),
                "c" * 40: _release("corrected")}
    def fetch(url, timeout):
        revision, name = url.split("/resolve/", 1)[1].split("/", 1)
        return io.BytesIO(releases[revision][0 if name == "manifest.json" else 1])
    monkeypatch.setattr(data, "urlopen", fetch)
    monkeypatch.setattr(data, "__file__", str(lock.with_name("data.py")))
    for revision, compatible in [("a", True), ("b", False), ("a", True), ("c", False)]:
        pinning.pin(tmp_path, "websearch", revision * 40)
        selected = json.loads(lock.read_bytes())["datasets"]["websearch"]
        assert selected["source_editions"]["b" * 40] == "a" * 40
        if compatible:
            assert data.legacy_dependencies("websearch", "b" * 40) == data.dependencies("websearch")
        else:
            with pytest.raises(ValueError, match="inputs changed"):
                data.legacy_dependencies("websearch", "b" * 40)


def test_mirror_and_explicit_token_apply_to_both_pinning_and_installation(tmp_path, monkeypatch):
    lock, _ = fixture(tmp_path, monkeypatch)
    manifest, archive = _release("original")
    calls = []
    def fetch(request, timeout):
        assert isinstance(request, Request)
        assert request.full_url.startswith("https://mirror.example/hub/datasets/")
        assert request.get_header("Authorization") == "Bearer hf_fixture"
        calls.append(request.full_url)
        return io.BytesIO(manifest if request.full_url.endswith("manifest.json") else archive)
    monkeypatch.setattr(data, "build_opener", lambda *handlers: SimpleNamespace(open=fetch))
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example/hub/")
    monkeypatch.setenv("HF_TOKEN", "hf_fixture")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("BROWSER_EVAL_DATASET_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(data, "__file__", str(lock.with_name("data.py")))
    data._resolve.cache_clear()
    try:
        pinning.pin(tmp_path, "websearch", "a" * 40)
        root = data.snapshot("websearch")
        assert json.loads((root / "definitions/datasets/sample.json").read_bytes())["cases"][0]["value"] == "original"
        assert len(calls) == 3  # Manifest + the same archive transport for pin and install.
    finally:
        data._resolve.cache_clear()


@pytest.mark.parametrize("target,token", [
    ("https://mirror.example/redirected", "Bearer hf_fixture"),
    ("https://cdn.example/object", None),
    ("http://mirror.example/object", None),
])
def test_hub_token_is_retained_only_on_same_origin_redirects(target, token):
    original = Request("https://mirror.example/file", headers={"Authorization": "Bearer hf_fixture"})
    redirected = data._TokenRedirects().redirect_request(original, None, 302, "Found", {}, target)
    assert redirected.get_header("Authorization") == token


@pytest.mark.parametrize("defect", ["short", "oversized", "hash"])
def test_shared_archive_reader_retains_size_and_hash_guards(monkeypatch, defect):
    raw = b"archive"
    specification = {"path": "definitions.tar.gz", "size": len(raw),
                     "sha256": hashlib.sha256(raw).hexdigest()}
    returned = raw[:-1] if defect == "short" else raw + b"x" if defect == "oversized" else b"changed"
    monkeypatch.setattr(data, "urlopen", lambda *args, **kwargs: io.BytesIO(returned))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(ValueError, match="size|hash"):
        data.fetch_archive("Lexmount/example", "a" * 40, copy.deepcopy(specification))
