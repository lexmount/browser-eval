from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.trajectory_replay.catalog_contracts import (
    ACCEPTANCE_SCHEMAS,
    RUN_SCHEMAS,
    file_sha256,
    repository_root,
    validate_catalog_comparison_release,
    validate_catalog_report_profile,
    validate_environment_preparation,
)


def test_current_contracts_keep_historical_run_and_acceptance_schemas_readable():
    assert "lex_webarena_full_replay_run/1" in RUN_SCHEMAS
    assert "lex_webarena_full_replay_run/2" in RUN_SCHEMAS
    assert "lex_webarena_full_acceptance_ledger/1" in ACCEPTANCE_SCHEMAS


def _files(tmp_path):
    profile = tmp_path / "environment.json"
    profile.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_replay_environment/1",
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


def test_environment_preparation_still_reads_legacy_profile_schema(tmp_path):
    receipt, profile = _files(tmp_path)
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["schema"] = "lex_webarena_verified_full_environment/1"
    profile.write_text(json.dumps(payload), encoding="utf-8")

    assert validate_environment_preparation(receipt, profile)["fresh_app_id"] == 202


def test_environment_preparation_accepts_complete_pinned_component_restart(tmp_path):
    receipt, profile = _files(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["method"] = "component_restarts_from_pinned_images"
    payload["reset_scope"] = ["shopping", "shopping_admin", "reddit", "gitlab"]
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    assert validate_environment_preparation(receipt, profile)["method"].startswith("component_")


def test_environment_preparation_rejects_partial_component_restart(tmp_path):
    receipt, profile = _files(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["method"] = "component_restarts_from_pinned_images"
    payload["reset_scope"] = ["shopping"]
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="reset scope"):
        validate_environment_preparation(receipt, profile)


def test_repository_root_is_independent_of_current_working_directory():
    root = repository_root(Path(__file__))
    assert (root / "pyproject.toml").is_file()
    assert (root / "harness/trajectory_replay/webarena_runtime_source.py").is_file()


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
    task_ids = [4, 11, 23]
    official_checksum = "official-data"
    dataset = _write_json(
        repository / "evaluation/full/dataset.json",
        {
            "schema": "lex_webarena_replay_dataset/1",
            "official_dataset_sha256": official_checksum,
            "task_count": len(task_ids),
            "evaluator_source": {
                "repository": fork,
                "branch": "lex-main",
                "revision": revision,
            },
        },
    )
    scope = _write_json(
        repository / "evaluation/full/scope.json",
        {
            "schema": "lex_webarena_replay_scope/1",
            "official_dataset_sha256": official_checksum,
            "task_count": len(task_ids),
            "task_ids": task_ids,
        },
    )
    data_home = tmp_path / "data"
    evidence = data_home / "trajectory-replay/run"
    counts = {
        "total": len(task_ids),
        "pass": len(task_ids),
        "unresolved": 0,
        "physical_attempts": 854,
        "excluded_duplicate_attempts": 0,
    }
    ledger = _write_json(
        evidence / "ledger.json",
        {
            "schema": "lex_trajectory_replay_catalog_acceptance/1",
            "scope_sha256": file_sha256(scope),
            "counts": counts,
        },
    )
    artifacts = {
        "manifest.json": {},
        "report.zip": {"fixture": True},
        "suite.json": {"tasks": []},
    }
    for name, value in artifacts.items():
        _write_json(evidence / name, value)
    catalog = _write_json(
        evidence / "catalog.json",
        {
            "schema": "lex_webarena_replay_catalog/1",
            "task_ids": task_ids,
            "accepted_ledger_sha256": file_sha256(ledger),
        },
    )
    profile = _write_json(
        repository / "evaluation/full/current-report.json",
        {
            "schema": "lex_webarena_replay_report_profile/1",
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

    assert validate_catalog_report_profile(profile, data_home)["accepted_counts"] == counts

    changed = json.loads(profile.read_text())
    changed["accepted_counts"]["physical_attempts"] += 1
    profile.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="acceptance population changed"):
        validate_catalog_report_profile(profile, data_home)

    changed = json.loads(dataset.read_text())
    changed["evaluator_source"]["repository"] = "https://github.com/ServiceNow/webarena-verified.git"
    dataset.write_text(json.dumps(changed))
    changed_profile = json.loads(profile.read_text())
    changed_profile["accepted_counts"] = counts
    changed_profile["dataset_sha256"] = file_sha256(dataset)
    profile.write_text(json.dumps(changed_profile))
    with pytest.raises(ValueError, match="dataset definition is invalid"):
        validate_catalog_report_profile(profile, data_home)


def test_comparison_release_binds_population_review_and_publication(tmp_path):
    data_home = tmp_path / "data"
    evidence = data_home / "trajectory-replay/comparison"
    profile = _write_json(
        evidence / "profile.json",
        {
            "schema": "lex_webarena_trajectory_replay_profile/1",
            "methods": [
                {"id": "moli", "engine": "moli", "label": "Moli"},
                {"id": "other", "engine": "other", "label": "Other"},
            ],
            "publication": {
                "site_slug": "site",
                "share_url": "https://artifact.example/v/share",
                "expected_version": "ver_2",
            },
        },
    )
    dataset = _write_json(
        evidence / "dataset.json",
        {
            "schema": "lex_webarena_catalog_comparison_dataset/1",
            "counts": {"verified_tasks": 2, "verified_trajectories": 2},
            "trajectories": [{"task_id": 1}, {"task_id": 2}],
        },
    )
    review = _write_json(evidence / "review.json", {"reviewed": 1})
    attempts = [
        {"participant_id": "moli", "engine": "moli", "task_id": 1, "status": "pass"},
        {"participant_id": "moli", "engine": "moli", "task_id": 2, "status": "pass"},
        {"participant_id": "other", "engine": "other", "task_id": 1, "status": "pass"},
        {"participant_id": "other", "engine": "other", "task_id": 2, "status": "fail"},
    ]
    results = _write_json(
        evidence / "results.json",
        {
            "schema": "lex_webarena_catalog_comparison_results/1",
            "dataset_sha256": file_sha256(dataset),
            "attempts": attempts,
            "physical_executions": [
                *attempts,
                {"participant_id": "other", "engine": "other", "task_id": 2},
            ],
        },
    )
    manifest = _write_json(
        evidence / "report/manifest.json",
        {
            "task_count": 2,
            "logical_results": 4,
            "failed_results": 1,
            "reviewed_failures": 1,
            "participants": ["moli", "other"],
            "results_sha256": file_sha256(results),
            "dataset_sha256": file_sha256(dataset),
            "review_sha256": file_sha256(review),
        },
    )
    archive = evidence / "report.zip"
    archive.write_bytes(b"archive")
    receipt = _write_json(
        evidence / "receipt.json",
        {
            "verified": True,
            "slug": "site",
            "share_url": "https://artifact.example/v/share",
            "version": "ver_2",
            "archive_sha256": file_sha256(archive),
        },
    )
    attestation = _write_json(
        evidence / "attestation.json",
        {
            "archive_sha256": file_sha256(archive),
            "profile_sha256": file_sha256(profile),
            "dataset_sha256": file_sha256(dataset),
            "review_sha256": file_sha256(review),
            "evidence_sha256": file_sha256(results),
        },
    )
    paths = {
        "profile": profile,
        "dataset": dataset,
        "results": results,
        "failure_review": review,
        "report_manifest": manifest,
        "publication_archive": archive,
        "publication_attestation": attestation,
        "publication_receipt": receipt,
    }
    binding = _write_json(
        tmp_path / "current-comparison.json",
        {
            "schema": "lex_webarena_replay_comparison_release/1",
            "evidence_root": "trajectory-replay/comparison",
            "task_count": 2,
            "logical_results": 4,
            "physical_executions": 5,
            "participants": [
                {
                    "id": "moli",
                    "engine": "moli",
                    "label": "Moli",
                    "pass": 2,
                    "fail": 0,
                },
                {
                    "id": "other",
                    "engine": "other",
                    "label": "Other",
                    "pass": 1,
                    "fail": 1,
                },
            ],
            "artifacts": {
                name: {"path": str(path.relative_to(evidence)), "sha256": file_sha256(path)}
                for name, path in paths.items()
            },
            "publication": {
                "slug": "site",
                "share_url": "https://artifact.example/v/share",
                "version_id": "ver_2",
            },
        },
    )

    assert validate_catalog_comparison_release(binding, data_home)["logical_results"] == 4

    changed = json.loads(results.read_text(encoding="utf-8"))
    changed["attempts"][3]["status"] = "pass"
    results.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="results artifact changed"):
        validate_catalog_comparison_release(binding, data_home)
