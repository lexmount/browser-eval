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
    runtime = json.loads(
        Path("examples/trajectory_replay/public_webarena/webarena.config.example.json").read_text(encoding="utf-8")
    )
    from urllib.parse import urlparse

    template = {
        "sites": {
            site: {
                "port": urlparse(runtime["environments"][f"__{site.upper()}__"]["urls"][0]).port,
                "control_port": urlparse(runtime["control_urls"][site]).port,
            }
            for site in compact["images"]
        },
        "credentials_env": {
            site: runtime["environments"][f"__{site.upper()}__"]["credentials_env"]
            for site in ("shopping_admin", "shopping", "reddit", "gitlab")
        },
    }
    (root / "config/runtime.example.json").write_text(json.dumps(template), encoding="utf-8")
    archive_names = ("osm_tile_server.tar", "nominatim_volumes.tar", "osrm_routing.tar")
    delivery = {
        "webarena_verified_version": compact["webarena_verified_version"],
        "image_lock": "images.lock.json",
        "components": {
            site: {"image": row["pinned"]}
            for site, row in compact["images"].items()
            if site != "wikipedia"
        }
    }
    delivery["components"]["map"].update(
        {
            "data_urls": [f"https://example.invalid/{name}" for name in archive_names],
            "data_archive_sha256": {name: "0" * 64 for name in archive_names},
            "data_archive_bytes": {name: 1 for name in archive_names},
        }
    )
    (root / "config/site-delivery-contract.json").write_text(
        json.dumps(delivery), encoding="utf-8"
    )
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


def test_cross_repo_contract_rejects_port_and_credential_drift(tmp_path):
    evaluation_lock = Path("evaluation/browser-use-public/webarena-environment-images.json")
    _write_environment_repo(tmp_path, evaluation_lock)
    path = tmp_path / "config/runtime.example.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sites"]["map"]["port"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="site port differs: map"):
        verify(tmp_path)
    payload["sites"]["map"]["port"] -= 1
    payload["credentials_env"]["shopping_admin"]["username"] = "WRONG_USERNAME"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="credential variable names differ: shopping_admin"):
        verify(tmp_path)


def test_cross_repo_contract_rejects_missing_map_digest(tmp_path):
    evaluation_lock = Path("evaluation/browser-use-public/webarena-environment-images.json")
    _write_environment_repo(tmp_path, evaluation_lock)
    path = tmp_path / "config/site-delivery-contract.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["components"]["map"]["data_archive_sha256"]["osrm_routing.tar"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Map archive checksum contract"):
        verify(tmp_path)


def test_cross_repo_contract_rejects_site_delivery_lock_path_drift(tmp_path):
    evaluation_lock = Path("evaluation/browser-use-public/webarena-environment-images.json")
    _write_environment_repo(tmp_path, evaluation_lock)
    path = tmp_path / "config/site-delivery-contract.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["image_lock"] = "../images.lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="image lock path differs"):
        verify(tmp_path)


def test_cross_repo_contract_reports_missing_site_delivery_contract(tmp_path):
    evaluation_lock = Path("evaluation/browser-use-public/webarena-environment-images.json")
    _write_environment_repo(tmp_path, evaluation_lock)
    (tmp_path / "config/site-delivery-contract.json").unlink()
    with pytest.raises(ValueError, match="missing site delivery contract"):
        verify(tmp_path)


def test_cross_repo_contract_reports_missing_image_lock_field(tmp_path):
    evaluation_lock = Path("evaluation/browser-use-public/webarena-environment-images.json")
    _write_environment_repo(tmp_path, evaluation_lock)
    path = tmp_path / "config/site-delivery-contract.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["image_lock"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="image_lock is missing"):
        verify(tmp_path)
