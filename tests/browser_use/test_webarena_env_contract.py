from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_webarena_env_contract import verify


def _write_environment_repo(root: Path, evaluation_lock: Path) -> Path:
    payload = json.loads(evaluation_lock.read_text(encoding="utf-8"))
    compact = {
        "schema": "lexmount_webarena_images/1",
        "webarena_verified_version": payload["webarena_verified_version"],
        "images": {
            site: {key: row[key] for key in ("pinned", "architecture", "os")}
            for site, row in payload["images"].items()
        },
    }
    path = root / "config/images.lock.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(compact), encoding="utf-8")
    return path


def test_cross_repo_contract_accepts_matching_images(tmp_path):
    evaluation_lock = Path("evaluation/browser-use-public/webarena-environment-images.json")
    _write_environment_repo(tmp_path, evaluation_lock)
    assert verify(tmp_path)["status"] == "pass"


def test_cross_repo_contract_rejects_one_changed_digest(tmp_path):
    evaluation_lock = Path("evaluation/browser-use-public/webarena-environment-images.json")
    path = _write_environment_repo(tmp_path, evaluation_lock)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["images"]["map"]["pinned"] = "example.invalid/map@sha256:" + "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="map.pinned"):
        verify(tmp_path)
