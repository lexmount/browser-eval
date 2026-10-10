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


@pytest.mark.parametrize("damage", [None, "tile", "route", "fabricated"])
def test_map_acceptance_checks_full_frozen_contract(tmp_path, monkeypatch, damage):
    import io
    import json
    from urllib.error import HTTPError

    from harness.environments.webarena import map_setup

    class Response(io.BytesIO):
        status = 200

    def get(url, **kwargs):
        if url.endswith(("/", "/login", "/api/capabilities")):
            return Response(b"synthetic entry point")
        if "/nominatim/search?" in url:
            data = [
                {
                    "lat": "40.44",
                    "lon": "-79.95",
                    "address": {"postcode": "15213"},
                    "display_name": "Carnegie Mellon University",
                }
            ]
        elif "/nominatim/reverse?" in url:
            data = {"address": {"city": "Pittsburgh"}}
        elif "/osrm/" in url:
            data = {"code": "Invalid" if damage == "route" else "Ok", "routes": [{"distance": 100}]}
        elif "/tile/" in url:
            return Response(
                b"not an image" if damage == "tile" else b"\x89PNG\r\n\x1a\n" + b"x" * 200
            )
        elif url.endswith("/relation/999999999999"):
            if damage == "fabricated":
                return Response(b"fabricated object")
            raise HTTPError(url, 404, "missing", {}, None)
        else:
            names = {
                "1479729158": "Bei Jing",
                "172206707": "Chatham University",
                "2279034": "Carnegie Mellon University",
                "5475586": "Lake Pontchartrain Causeway",
            }
            identifier = url.rsplit("/", 1)[1]
            return Response(f"<!doctype html>{identifier} {names[identifier]}".encode())
        return Response(json.dumps(data).encode())

    monkeypatch.setattr(map_setup, "urlopen", get)
    output = tmp_path / "receipt.json"
    if damage:
        with pytest.raises(ValueError):
            map_setup.map_check_main(["--url", "https://map.example", "--output", str(output)])
        assert not output.exists()
        return
    map_setup.map_check_main(["--url", "https://map.example", "--output", str(output)])
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "pass" and len(receipt["checks"]) == 14
    monkeypatch.setattr(
        map_setup,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing output must fail before HTTP")
        ),
    )
    with pytest.raises(ValueError, match="output already exists"):
        map_setup.map_check_main(["--url", "https://map.example", "--output", str(output)])


def test_map_acceptance_remains_enforced_under_python_optimization():
    import subprocess
    import sys

    code = """
import io
from harness.environments.webarena import map_setup
class Response(io.BytesIO):
    status = 200
map_setup.urlopen = lambda *args, **kwargs: Response(b'[]')
try:
    map_setup.verify_map_endpoint('https://map.example')
except ValueError as error:
    if 'CMU search result missing' not in str(error):
        raise RuntimeError('wrong rejection')
else:
    raise RuntimeError('optimized Python skipped acceptance')
"""
    result = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
