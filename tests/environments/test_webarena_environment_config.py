from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from harness.environments.webarena import config as owner
from harness.environments.webarena.config import RuntimeConfig, normalized_architecture


@pytest.mark.parametrize("digest", ["sha256:" + "a" * 64, "sha256:short", "sha256:" + "z" * 64, ""])
def test_image_resolution_requires_complete_immutable_digest(monkeypatch, digest):
    calls = []

    def registry(url, *, headers=None):
        calls.append((url, headers))
        if "auth.docker.io" in url:
            return {"token": "synthetic-token"}, {}
        if "/manifests/" in url:
            return {
                "layers": [{"size": 12}, {"size": 18}],
                "config": {"digest": "sha256:" + "b" * 64},
            }, {"Docker-Content-Digest": digest}
        return {"architecture": "arm64", "os": "linux"}, {}

    monkeypatch.setattr(owner, "_registry_json", registry)
    if len(digest) != 71 or "z" in digest:
        with pytest.raises(ValueError, match="immutable digest"):
            owner.resolve_image("example/browser:stable")
    else:
        result = owner.resolve_image("example/browser:stable")
        assert result["pinned"] == "example/browser@" + digest
        assert result["architecture"] == "arm64"
        assert result["compressed_layer_bytes"] == 30
        assert calls[1][1]["Authorization"] == "Bearer synthetic-token"


def test_image_lock_command_uses_environment_registry_and_preserves_existing_output(
    tmp_path, monkeypatch
):
    calls = []

    def resolve(reference):
        calls.append(reference)
        return {"tag": reference, "pinned": reference.split(":")[0] + "@sha256:" + "a" * 64}

    monkeypatch.setattr(owner, "resolve_image", resolve)
    output = tmp_path / "images.json"
    owner.main_image_lock(["--output", str(output)])
    before = output.read_bytes()
    assert set(json.loads(before)["images"]) == set(owner.SITES)
    assert calls == list(owner.official_image_references().values())
    calls.clear()
    with pytest.raises(ValueError, match="output already exists"):
        owner.main_image_lock(["--output", str(output)])
    assert not calls
    assert output.read_bytes() == before


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("matches", [True, False])
def test_registry_digest_matches_original_response_bytes(monkeypatch, matches):
    raw = b'{ "schemaVersion": 2 }\n'

    class Response(io.BytesIO):
        headers = {
            "Docker-Content-Digest": "sha256:"
            + (hashlib.sha256(raw).hexdigest() if matches else "0" * 64)
        }

    monkeypatch.setattr(owner.urllib.request, "urlopen", lambda *a, **kw: Response(raw))
    if matches:
        value, headers = owner._registry_json("https://registry.example.invalid/manifest")
        assert value == {"schemaVersion": 2}
        assert headers["Docker-Content-Digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    else:
        with pytest.raises(ValueError, match="does not match received bytes"):
            owner._registry_json("https://registry.example.invalid/manifest")


def _runtime(tmp_path: Path, *, hostname: str = "127.0.0.1") -> Path:
    lock = json.loads(
        (ROOT / "evaluation/browser-use-public/webarena-environment-images.json").read_text(
            encoding="utf-8"
        )
    )
    (tmp_path / "images.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    runtime = json.loads(
        (ROOT / "deploy/webarena/runtime.example.json").read_text(encoding="utf-8")
    )
    runtime["hostname"] = hostname
    runtime["image_lock"] = "images.lock.json"
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(runtime), encoding="utf-8")
    return path


def test_runtime_loads_every_digest_pinned_site(tmp_path: Path) -> None:
    config = RuntimeConfig.load(_runtime(tmp_path))

    assert set(config.sites) == {
        "shopping_admin",
        "shopping",
        "reddit",
        "gitlab",
        "map",
        "wikipedia",
    }
    assert config.sites["gitlab"].architecture == "amd64"
    assert config.sites["wikipedia"].architecture == "arm64"
    assert all("@sha256:" in site.image for site in config.sites.values())


def test_example_hostname_must_be_replaced() -> None:
    with pytest.raises(ValueError, match="replace the example placeholder"):
        RuntimeConfig.load(ROOT / "deploy/webarena/runtime.example.json")


def test_architecture_aliases_cover_intel_and_amd_x86_hosts() -> None:
    assert normalized_architecture("x86_64") == "amd64"
    assert normalized_architecture("amd64") == "amd64"
    assert normalized_architecture("aarch64") == "arm64"


def test_missing_locked_architecture_fails_before_docker_use(tmp_path: Path) -> None:
    path = _runtime(tmp_path)
    lock_path = tmp_path / "images.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["images"]["map"]["architecture"] = ""
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(ValueError, match="explicitly amd64 or arm64"):
        RuntimeConfig.load(path)
