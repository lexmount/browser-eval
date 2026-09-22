from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.environments.webarena.config import RuntimeConfig, normalized_architecture

ROOT = Path(__file__).resolve().parents[2]


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
