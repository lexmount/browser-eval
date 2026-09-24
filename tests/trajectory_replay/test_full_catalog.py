from __future__ import annotations

import json

import pytest

from harness.trajectory_replay.full_catalog import (
    file_sha256,
    validate_environment_preparation,
    validate_full_report_profile,
)


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


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_report_profile_owns_accepted_counts_instead_of_validator_code(tmp_path):
    repository = tmp_path / "repository"
    (repository / "pyproject.toml").parent.mkdir(parents=True)
    revision = "a" * 40
    fork = "https://github.com/lexmount/webarena-verified.git"
    (repository / "pyproject.toml").write_text(
        f"[project]\nname='fixture'\n[tool.uv.sources]\n"
        f"webarena-verified = {{ git = '{fork}', rev = '{revision}' }}\n"
    )
    (repository / "uv.lock").write_text(
        f"version = 1\n[[package]]\nname = 'webarena-verified'\n"
        f"source = {{ git = '{fork}?rev={revision}#{revision}' }}\n"
    )
    dataset = _write_json(
        repository / "evaluation/full/dataset.json",
        {
            "schema": "lex_webarena_verified_full_dataset/1",
            "task_count": 812,
            "task_id_range": [0, 811],
            "evaluator_source": {
                "repository": fork,
                "branch": "lex-main",
                "revision": revision,
            },
        },
    )
    scope = _write_json(repository / "evaluation/full/scope.json", {"task_ids": list(range(812))})
    data_home = tmp_path / "data"
    evidence = data_home / "trajectory-replay/run"
    counts = {
        "total": 812,
        "pass": 812,
        "unresolved": 0,
        "physical_attempts": 854,
        "excluded_duplicate_attempts": 0,
    }
    ledger = _write_json(evidence / "ledger.json", {"counts": counts})
    artifacts = {
        "manifest.json": {},
        "report.zip": {"fixture": True},
        "suite.json": {"tasks": []},
    }
    for name, value in artifacts.items():
        _write_json(evidence / name, value)
    catalog = _write_json(
        evidence / "catalog.json",
        {"accepted_ledger_sha256": file_sha256(ledger)},
    )
    profile = _write_json(
        repository / "evaluation/full/current-report.json",
        {
            "schema": "lex_webarena_verified_full_report_profile/1",
            "dataset_path": "evaluation/full/dataset.json",
            "dataset_sha256": file_sha256(dataset),
            "scope_path": "evaluation/full/scope.json",
            "scope_sha256": file_sha256(scope),
            "evidence_root": "trajectory-replay/run",
            "acceptance_ledger_path": "ledger.json",
            "acceptance_ledger_sha256": file_sha256(ledger),
            "accepted_counts": counts,
            "report_manifest_path": "manifest.json",
            "report_manifest_sha256": file_sha256(evidence / "manifest.json"),
            "report_archive_path": "report.zip",
            "report_archive_sha256": file_sha256(evidence / "report.zip"),
            "accepted_catalog": {
                "path": "catalog.json",
                "sha256": file_sha256(catalog),
            },
            "accepted_portable_suite": {
                "path": "suite.json",
                "sha256": file_sha256(evidence / "suite.json"),
            },
            "publication": {
                "host": "https://artifact.example",
                "slug": "fixture",
                "version_id": "v1",
                "share_url": "https://artifact.example/v/fixture",
            },
        },
    )

    assert validate_full_report_profile(profile, data_home)["accepted_counts"] == counts

    changed = json.loads(profile.read_text())
    changed["accepted_counts"]["physical_attempts"] += 1
    profile.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="acceptance population changed"):
        validate_full_report_profile(profile, data_home)

    changed = json.loads(dataset.read_text())
    changed["evaluator_source"]["repository"] = "https://github.com/ServiceNow/webarena-verified.git"
    dataset.write_text(json.dumps(changed))
    changed_profile = json.loads(profile.read_text())
    changed_profile["accepted_counts"] = counts
    changed_profile["dataset_sha256"] = file_sha256(dataset)
    profile.write_text(json.dumps(changed_profile))
    with pytest.raises(ValueError, match="dataset definition is invalid"):
        validate_full_report_profile(profile, data_home)
