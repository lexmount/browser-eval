from __future__ import annotations

import json
import shutil

import pytest

from scripts.verify_webarena_env_contract import ROOT, verify


def _checkout(tmp_path):
    for name in (
        "evaluation/browser-use-public/webarena-environment-images.json",
        "examples/trajectory_replay/public_webarena/webarena.config.example.json",
        "examples/trajectory_replay/public_webarena/rainbond-pool.example.json",
        "deploy/webarena/runtime.example.json",
        "deploy/webarena/site-delivery-contract.json",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return tmp_path


def test_repository_contract_uses_one_image_lock():
    assert verify()["status"] == "pass"


@pytest.mark.parametrize("template", ["webarena.config", "rainbond-pool"])
def test_evaluation_templates_reject_wrong_relative_lock_path(tmp_path, template):
    root = _checkout(tmp_path)
    path = root / f"examples/trajectory_replay/public_webarena/{template}.example.json"
    data = json.loads(path.read_text())
    data["image_lock"] = "../../evaluation/browser-use-public/webarena-environment-images.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="canonical image lock"):
        verify(root)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("image_lock", "other.json", "canonical image lock"),
        ("webarena_verified_version", "0.0.0", "package version"),
    ],
)
def test_delivery_rejects_wrong_binding(tmp_path, field, value, message):
    root = _checkout(tmp_path)
    path = root / "deploy/webarena/site-delivery-contract.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        verify(root)


def test_deployment_rejects_port_drift(tmp_path):
    root = _checkout(tmp_path)
    path = root / "deploy/webarena/runtime.example.json"
    data = json.loads(path.read_text())
    data["sites"]["reddit"]["port"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="site port differs: reddit"):
        verify(root)


def test_deployment_rejects_duplicate_image_identity(tmp_path):
    root = _checkout(tmp_path)
    path = root / "deploy/webarena/site-delivery-contract.json"
    data = json.loads(path.read_text())
    data["components"]["reddit"]["image"] = "reddit:latest"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="canonical image identity"):
        verify(root)


def test_map_contract_rejects_missing_checksum(tmp_path):
    root = _checkout(tmp_path)
    path = root / "deploy/webarena/site-delivery-contract.json"
    data = json.loads(path.read_text())
    del data["components"]["map"]["data_archive_sha256"]["osrm_routing.tar"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Map archive checksum"):
        verify(root)
