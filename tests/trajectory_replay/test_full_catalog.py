from __future__ import annotations

import json

import pytest

from harness.trajectory_replay.full_catalog import validate_environment_preparation


def _files(tmp_path):
    profile = tmp_path / "environment.json"
    profile.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_verified_full_environment/1",
                "deployment": {
                    "acceptance_app_id": 202,
                    "source_app_id": 195,
                    "site_reset_between_tasks": False,
                },
                "images": {
                    site: f"sha256:{site}"
                    for site in ("shopping", "shopping_admin", "reddit", "gitlab")
                },
            }
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_environment_preparation/1",
                "method": "new_rainbond_application_from_pinned_images",
                "source_app_id": 195,
                "fresh_app_id": 202,
                "site_component_images": {
                    site: f"registry.example/image@sha256:{site}"
                    for site in ("shopping", "shopping_admin", "reddit", "gitlab")
                },
                "database_volume_mounts": {
                    site: 0 for site in ("shopping", "shopping_admin", "reddit", "gitlab")
                },
                "initialization": {
                    site: "ready" for site in ("shopping", "shopping_admin", "reddit", "gitlab")
                },
                "public_http_status": {
                    site: 200 for site in ("shopping", "shopping_admin", "reddit", "gitlab")
                },
                "shared_site_components": ["map", "wikipedia"],
                "site_reset_between_tasks": False,
            }
        ),
        encoding="utf-8",
    )
    return receipt, profile


def test_environment_preparation_is_bound_to_versioned_profile(tmp_path):
    receipt, profile = _files(tmp_path)
    assert validate_environment_preparation(receipt, profile)["fresh_app_id"] == 202


@pytest.mark.parametrize(
    ("path", "key", "value"),
    [
        ("receipt", "fresh_app_id", 203),
        ("receipt", "method", "unverified_reset"),
        ("profile", "schema", "unknown"),
    ],
)
def test_environment_preparation_rejects_unbound_receipts(tmp_path, path, key, value):
    receipt, profile = _files(tmp_path)
    target = receipt if path == "receipt" else profile
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload[key] = value
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="declared deployment"):
        validate_environment_preparation(receipt, profile)
