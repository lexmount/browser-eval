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


def load_sample_contract(source: Path, manifest_path: Path, profile_path: Path):
    """Read a predeclared sample and method matrix without assuming its size or providers."""
    from .webmainbench_runner import load_source

    for path in (source, manifest_path, profile_path):
        contained_path(path.parent, path.name, reject_symlinks=True)
    profile = json.loads(profile_path.read_bytes())
    manifest = json.loads(manifest_path.read_bytes())
    providers = [safe_identifier(name) for name in profile["providers"]]
    ids = [safe_identifier(row["track_id"]) for row in manifest.get("cases", [])]
    if (not providers or len(set(providers)) != len(providers)
            or not ids or len(set(ids)) != len(ids)
            or manifest.get("schema_version") != 1
            or manifest.get("sampling", {}).get("size") != len(ids)
            or profile.get("case_count") != len(ids)
            or profile.get("runs_per_case") != 1 or profile.get("concurrency") != 1
            or profile.get("source_dataset_sha256") != manifest.get("parent", {}).get("sha256")
            or profile.get("sample_manifest_sha256") != digest(manifest_path)):
        raise ValueError("Sample manifest and declared profile differ")
    records = load_source(source, set(ids), profile["source_dataset_sha256"],
                          expected_count=manifest["parent"].get("records"))
    return records, manifest, profile


def scored_shard_bindings(root: Path, providers: list[str], *, shards=None) -> dict[str, str]:
    """Bind exactly the review-visible records, summaries, execution and output bytes."""
    bindings = {}
    for provider in providers:
        shard = (contained_path(root, safe_identifier(provider), reject_symlinks=True)
                 if shards is None else contained_path(shards[provider].parent, shards[provider].name,
                                                       reject_symlinks=True))
        paths = {name for name in ("dataset.json", "results.jsonl", "scored-results.jsonl",
                                  "physical-attempts.jsonl", "score-summary.json", "execution.json")}
        for name, key in (("results.jsonl", "artifact_path"),
                          ("scored-results.jsonl", "artifact_path"),
                          ("physical-attempts.jsonl", "physical_artifact_path")):
            for row in read_jsonl(contained_path(shard, name, reject_symlinks=True)):
                paths.add(row[key])
        execution = json.loads(contained_path(shard, "execution.json", reject_symlinks=True).read_bytes())
        if execution.get("fixture_ca_artifact"):
            paths.add(execution["fixture_ca_artifact"])
        for correction in execution.get("correction_runs", []):
            if correction.get("fixture_ca_sha256"):
                paths.add("correction-fixture-ca.pem")
        for relative in sorted(paths):
            path = contained_path(shard, relative, reject_symlinks=True)
            bindings[f"{provider}/{relative}"] = digest(path)
    return bindings
