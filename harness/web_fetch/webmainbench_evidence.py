"""Shared physical-call binding checks for WebMainBench evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

from .safety import contained_path, safe_identifier


def retry_chain_matches(row: dict, attempts: list[dict]) -> bool:
    correction = row.get("provider_metadata", {}).get("latency_correction", {})
    recorded_elapsed = correction.get("original_elapsed_ms", row["elapsed_ms"])
    return bool(attempts) and (
        len(attempts) == row["physical_attempt_count"]
        and [item["physical_attempt"] for item in attempts] == list(range(1, len(attempts) + 1))
        and attempts[-1]["output_sha256"] == row["output_sha256"]
        and abs(attempts[-1]["elapsed_ms"] - recorded_elapsed) <= 1e-12
        and abs(sum(item["elapsed_ms"] for item in attempts) - row["total_elapsed_ms"]) <= 1e-12
    )


def alias_row(row: dict, source_provider: str, provider: str) -> dict:
    safe_identifier(source_provider)
    safe_identifier(provider)
    row = copy.deepcopy(row)
    row["provider"] = provider
    row["attempt_id"] = row["attempt_id"].replace(source_provider + "__", provider + "__", 1)
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


def require_matrix(rows: list[dict], providers: list[str], case_ids: set[str]) -> None:
    expected = {(provider, case_id) for provider in providers for case_id in case_ids}
    actual = {(row["provider"], row["case_id"]) for row in rows}
    attempts = [row["attempt_id"] for row in rows]
    if actual != expected or len(rows) != len(expected):
        raise ValueError("Logical result matrix is incomplete")
    if len(attempts) != len(set(attempts)):
        raise ValueError("Logical attempt identity is not unique")


def require_provider_batch(execution: dict, provider: str) -> dict:
    batches = [batch for batch in execution.get("batches", []) if batch.get("provider") == provider]
    if len(batches) != 1:
        raise ValueError(
            f"Expected exactly one execution batch for {provider}, found {len(batches)}"
        )
    return copy.deepcopy(batches[0])


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
