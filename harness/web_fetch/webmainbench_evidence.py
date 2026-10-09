"""Shared physical-call binding checks for WebMainBench evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

from harness.record_io import load_jsonl

from .safety import contained_path, safe_identifier


def retry_chain_matches(row: dict, attempts: list[dict]) -> bool:
    correction = row.get("provider_metadata", {}).get("latency_correction", {})
    recorded_elapsed = correction.get("original_elapsed_ms", row["elapsed_ms"])
    return bool(attempts) and (
        len(attempts) == row["physical_attempt_count"]
        and [item["physical_attempt"] for item in attempts]
        == list(range(1, len(attempts) + 1))
        and attempts[-1]["output_sha256"] == row["output_sha256"]
        and abs(attempts[-1]["elapsed_ms"] - recorded_elapsed) <= 1e-12
        and abs(sum(item["elapsed_ms"] for item in attempts) - row["total_elapsed_ms"])
        <= 1e-12
    )


def alias_row(row: dict, source_provider: str, provider: str) -> dict:
    safe_identifier(source_provider)
    safe_identifier(provider)
    row = copy.deepcopy(row)
    row["provider"] = provider
    row["attempt_id"] = row["attempt_id"].replace(
        source_provider + "__", provider + "__", 1
    )
    for key in ("artifact_path", "physical_artifact_path"):
        if key in row:
            row[key] = row[key].replace(
                f"artifacts/{source_provider}/", f"artifacts/{provider}/", 1
            )
    if row.get("supersedes_attempt_id"):
        row["supersedes_attempt_id"] = row["supersedes_attempt_id"].replace(
            source_provider + "__", provider + "__", 1
        )
    return row


def copy_alias_artifacts(
    source: Path, output: Path, rows: list[dict], source_provider: str, provider: str
) -> None:
    safe_identifier(source_provider)
    safe_identifier(provider)
    if source.is_symlink() or output.is_symlink():
        raise ValueError("Symlinked variant evidence is forbidden")
    for row in rows:
        expected_sha256 = row.get("output_sha256")
        if not isinstance(expected_sha256, str) or not expected_sha256:
            raise ValueError(
                "Variant artifact is missing output_sha256: "
                + str(row.get("attempt_id", "unknown attempt"))
            )
        for key in ("artifact_path", "physical_artifact_path"):
            if key not in row:
                continue
            original = contained_path(source, row[key], reject_symlinks=True)
            if not row[key].startswith(f"artifacts/{source_provider}/"):
                raise ValueError("Variant artifact belongs to a different provider")
            if digest(original) != expected_sha256:
                raise ValueError("Variant artifact changed: " + row[key])
            relative = row[key].replace(
                f"artifacts/{source_provider}/", f"artifacts/{provider}/", 1
            )
            target = contained_path(output, relative, reject_symlinks=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            if digest(target) != expected_sha256:
                raise ValueError("Variant artifact changed while copying: " + row[key])


def copy_bound(
    root: Path,
    output: Path,
    relative: str,
    expected: str,
    *,
    target_relative: str | None = None,
) -> None:
    source = contained_path(root, relative, reject_symlinks=True)
    if digest(source) != expected:
        raise ValueError("Evidence artifact changed: " + relative)
    target = contained_path(output, target_relative or relative, reject_symlinks=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if digest(target) != expected:
        raise ValueError("Evidence changed while copying: " + relative)


def require_matrix(rows: list[dict], providers: list[str], case_ids: set[str]) -> None:
    expected = {(provider, case_id) for provider in providers for case_id in case_ids}
    actual = {(row["provider"], row["case_id"]) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError("Logical result matrix is incomplete")
    require_unique_attempt_ids(rows, "Logical")


def require_provider_batch(execution: dict, provider: str) -> dict:
    batches = [
        batch
        for batch in execution.get("batches", [])
        if batch.get("provider") == provider
    ]
    if len(batches) != 1:
        raise ValueError(
            f"Expected exactly one execution batch for {provider}, found {len(batches)}"
        )
    return copy.deepcopy(batches[0])


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


def require_unique_attempt_ids(rows: list[dict], label: str) -> None:
    values = [row.get("attempt_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in values) or len(
        set(values)
    ) != len(values):
        raise ValueError(f"{label} attempt identity is not unique")


def read_method_binding(root: Path) -> tuple[str, dict]:
    """Read the current binding or the profile-bound historical release format."""
    names = [
        name
        for name in ("method-binding.json", "seven-method-binding.json")
        if (root / name).is_file()
    ]
    if len(names) != 1:
        raise ValueError("Exactly one method-cohort binding is required")
    path = contained_path(root, names[0], reject_symlinks=True)
    binding = json.loads(path.read_text(encoding="utf-8"))
    formats = {
        "method-binding.json": (2, "webmainbench_method_cohort_binding"),
        "seven-method-binding.json": (
            1,
            "webmainbench_seven_method_full_scope_binding",
        ),
    }
    if (
        not isinstance(binding, dict)
        or (binding.get("schema_version"), binding.get("kind")) != formats[names[0]]
    ):
        raise ValueError("Unsupported method-cohort binding format")
    return names[0], binding
