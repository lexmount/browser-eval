"""Map data volumes may only be initialized from locked archive bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.environments.webarena.map_archives import DEFAULT_CONTRACT, verify_archives


def _contract(tmp_path: Path, *, wrong_digest: bool = False) -> Path:
    archives = {"osm_tile_server.tar": b"tile", "nominatim_volumes.tar": b"geo"}
    for name, data in archives.items():
        (tmp_path / name).write_bytes(data)
    checksums = {name: hashlib.sha256(data).hexdigest() for name, data in archives.items()}
    if wrong_digest:
        checksums["nominatim_volumes.tar"] = "0" * 64
    contract = {
        "components": {
            "map": {
                "data_urls": [f"https://example.invalid/{name}" for name in archives],
                "data_archive_sha256": checksums,
                "data_archive_bytes": {name: len(data) for name, data in archives.items()},
            }
        }
    }
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def test_map_archive_verifier_accepts_exact_local_bytes(tmp_path: Path) -> None:
    rows = verify_archives(tmp_path, _contract(tmp_path))
    assert [row["archive"] for row in rows] == [
        "osm_tile_server.tar",
        "nominatim_volumes.tar",
    ]


def test_map_archive_verifier_uses_canonical_contract_outside_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _contract(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert DEFAULT_CONTRACT.is_absolute() and DEFAULT_CONTRACT.is_file()
    with pytest.raises(ValueError, match="size mismatch: osm_tile_server.tar"):
        verify_archives(tmp_path)


def test_map_archive_verifier_rejects_wrong_bytes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nominatim_volumes.tar"):
        verify_archives(tmp_path, _contract(tmp_path, wrong_digest=True))


def test_map_archive_verifier_rejects_missing_file(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    (tmp_path / "osm_tile_server.tar").unlink()
    with pytest.raises(FileNotFoundError):
        verify_archives(tmp_path, contract)


def test_map_archive_verifier_rejects_truncated_file_before_hashing(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    (tmp_path / "osm_tile_server.tar").write_bytes(b"bad")
    with pytest.raises(ValueError, match="size mismatch: osm_tile_server.tar"):
        verify_archives(tmp_path, contract)


def test_map_archive_verifier_rejects_inventory_drift(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    payload = json.loads(contract.read_text(encoding="utf-8"))
    payload["components"]["map"]["data_urls"].append("https://example.invalid/other.tar")
    contract.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="inventory"):
        verify_archives(tmp_path, contract)
