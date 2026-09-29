"""Shared construction and validation for the certified risk-control cohort."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .safety import contained_path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_ids_digest(case_ids: list[str]) -> str:
    return hashlib.sha256(("\n".join(case_ids) + "\n").encode()).hexdigest()


def risk_control_comparison(profile: dict[str, Any], profile_sha256: str) -> dict[str, Any]:
    execution = profile["execution"]
    return {
        "kind": "risk_control_100_moli",
        "run_profile_sha256": profile_sha256,
        "timeout_seconds": execution["timeout_seconds"],
        "runs_per_case": execution["runs_per_case"],
        "concurrency": execution["concurrency"],
        "max_failure_retries": execution["max_failure_retries"],
        "conversion": "Moli native Markdown; wait-until done; no per-case options",
        "require_independent_result_review": False,
    }


def measured_risk_control_dataset(
    profile: dict[str, Any], frozen_dataset: dict[str, Any], profile_sha256: str
) -> dict[str, Any]:
    return {
        **frozen_dataset,
        "coverage_plan": {"risk_control": [profile["provider"]["name"]]},
        "comparison": risk_control_comparison(profile, profile_sha256),
    }


def validate_risk_control_certification(
    source: Path,
    dataset: dict[str, Any],
    records: list[dict[str, Any]],
    receipt: dict[str, Any],
    profile_path: Path,
) -> None:
    """Reject any evidence that is not the exact profile-bound 100-by-1 matrix."""
    profile_bytes = profile_path.read_bytes()
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    profile = json.loads(profile_bytes)
    repository_root = profile_path.parents[2]
    frozen_dataset_path = contained_path(repository_root, profile["dataset"]["path"])
    frozen_dataset_bytes = frozen_dataset_path.read_bytes()
    frozen_dataset_sha256 = hashlib.sha256(frozen_dataset_bytes).hexdigest()
    frozen_dataset = json.loads(frozen_dataset_bytes)
    expected_case_ids = [case["id"] for case in frozen_dataset["cases"]]
    provider_name = profile["provider"]["name"]
    expected_identity = {
        "reported_version": profile["provider"]["reported_version"],
        "binary_sha256": profile["provider"]["binary_sha256"],
    }
    execution = profile["execution"]

    if (
        profile.get("schema_version") != 1
        or profile.get("kind") != "risk_control_100_moli_v1"
        or frozen_dataset_sha256 != profile["dataset"]["sha256"]
        or len(expected_case_ids) != profile["dataset"]["case_count"]
        or len(expected_case_ids) != 100
        or len(expected_case_ids) != len(set(expected_case_ids))
        or _case_ids_digest(expected_case_ids) != profile["dataset"]["case_ids_sha256"]
        or frozen_dataset.get("site_count") != profile["dataset"]["site_count"]
    ):
        raise ValueError("Risk-control profile or frozen dataset is inconsistent")

    measured_base = {
        key: value for key, value in dataset.items() if key not in {"coverage_plan", "comparison"}
    }
    if (
        measured_base != frozen_dataset
        or dataset.get("coverage_plan") != {"risk_control": [provider_name]}
        or dataset.get("comparison") != risk_control_comparison(profile, profile_sha256)
    ):
        raise ValueError("Risk-control measured dataset does not match the frozen profile")

    expected_pairs = {(provider_name, case_id) for case_id in expected_case_ids}
    observed_pairs = [(record.get("provider"), record.get("case_id")) for record in records]
    expected_attempt_ids = [f"{provider_name}__{case_id}__r001" for case_id in expected_case_ids]
    if (
        len(records) != 100
        or len(observed_pairs) != len(set(observed_pairs))
        or set(observed_pairs) != expected_pairs
        or [record.get("attempt_id") for record in records] != expected_attempt_ids
        or any(record.get("run") != 1 for record in records)
    ):
        raise ValueError("Risk-control results are not the complete unique 100-by-1 matrix")
    for record in records:
        metadata = record.get("provider_metadata", {})
        if (
            metadata.get("reported_version") != expected_identity["reported_version"]
            or metadata.get("binary_sha256") != expected_identity["binary_sha256"]
            or "retry" in metadata
        ):
            raise ValueError("Risk-control result provider identity or retry policy changed")

    manifest_path = contained_path(source, "manifest.json")
    execution_path = contained_path(source, "execution.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    execution_record = json.loads(execution_path.read_text(encoding="utf-8"))
    provider_rows = manifest.get("providers")
    parameters = manifest.get("parameters", {})
    if (
        manifest.get("attempt_count") != 100
        or manifest.get("attempt_ids") != expected_attempt_ids
        or manifest.get("unexecuted") != []
        or manifest.get("dataset_sha256") != frozen_dataset_sha256
        or manifest.get("provider_config_sha256") != profile["provider"]["config_sha256"]
        or not isinstance(provider_rows, list)
        or len(provider_rows) != 1
        or provider_rows[0].get("name") != provider_name
        or provider_rows[0].get("implementation_identity") != expected_identity
        or parameters.get("runs") != execution["runs_per_case"]
        or parameters.get("timeout") != execution["timeout_seconds"]
        or parameters.get("delay") != execution["delay_seconds"]
        or parameters.get("provider_concurrency") != execution["concurrency"]
        or parameters.get("max_failure_retries") != execution["max_failure_retries"]
    ):
        raise ValueError("Risk-control manifest does not match the frozen execution contract")
    aggregate = execution_record.get("aggregate", {})
    if (
        execution_record.get("schema_version") != 1
        or execution_record.get("batches") != [manifest]
        or aggregate.get("providers") != [provider_name]
        or aggregate.get("case_ids") != expected_case_ids
        or aggregate.get("batch_count") != 1
        or aggregate.get("started_at") != manifest.get("started_at")
        or not isinstance(aggregate.get("completed_at"), str)
        or not aggregate["completed_at"]
    ):
        raise ValueError("Risk-control execution record is incomplete or mismatched")

    results_path = contained_path(source, "results.jsonl")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "certified_risk_control_100_moli_v1"
        or receipt.get("profile_sha256") != profile_sha256
        or receipt.get("dataset_sha256") != _digest(contained_path(source, "dataset.json"))
        or receipt.get("results_sha256") != _digest(results_path)
        or receipt.get("manifest_sha256") != _digest(manifest_path)
        or receipt.get("execution_sha256") != _digest(execution_path)
        or receipt.get("provider_identity_before") != expected_identity
        or receipt.get("provider_identity_after") != expected_identity
        or receipt.get("logical_results") != 100
        or receipt.get("physical_calls") != 100
        or receipt.get("complete_diagnostic_logs") is not True
    ):
        raise ValueError("Risk-control certification receipt is incomplete or mismatched")
