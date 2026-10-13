"""Compile one hash-bound Web Fetch dataset from its registered source datasets."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.retrieval_hints import native_fetch_dataset
from harness.web_fetch.safety import contained_path

ROOT = Path(__file__).resolve().parents[2]

_CORRECTABLE_CASE_FIELDS = {
    "ground_truth_main_content",
    "ground_truth_evidence",
    "source_verification",
    "body_requirements",
    "required_patterns",
    "required_facts",
    "acceptance_contract",
    "composite_acceptance",
    "forbidden_any",
    "mutable_body_policy",
}

_RERUN_ONLY_CASE_FIELDS = {
    "url",
    "description",
    "task_prompt",
    "reference_answer",
    "source_benchmark",
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _case_sha256(case: dict) -> str:
    encoded = json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(encoded)


def _apply_case_corrections(
    definition: dict, selected: dict[str, dict], repository: Path
) -> list[str]:
    """Apply compact, hash-locked reference and scoring corrections to imported cases."""
    from harness.web_fetch.scoring_regressions import require_scoring_regressions

    declaration = definition.get("case_corrections")
    if declaration is None:
        return []
    if set(declaration) != {"path", "sha256"}:
        raise ValueError("Assembly case-correction declaration is invalid")
    path = contained_path(repository, declaration["path"], reject_symlinks=True)
    raw = path.read_bytes()
    if _sha256(raw) != declaration["sha256"]:
        raise ValueError("Assembly case corrections changed: " + declaration["path"])
    document = json.loads(raw)
    schema_version = document.get("schema_version")
    if (
        schema_version not in {1, 2}
        or document.get("kind") != "profile_case_corrections"
        or not isinstance(document.get("cases"), list)
    ):
        raise ValueError("Unsupported profile case corrections")
    seen = set()
    rerun_all_methods = []
    for row in document["cases"]:
        expected_fields = {"case_id", "expected_definition_sha256", "set", "evidence"}
        if schema_version == 2:
            expected_fields.add("execution_effect")
        if set(row) != expected_fields:
            raise ValueError("Assembly case correction is invalid")
        case_id = row["case_id"]
        if case_id in seen or case_id not in selected:
            raise ValueError("Assembly case correction names an unknown or duplicate case")
        seen.add(case_id)
        case = selected[case_id]
        if _case_sha256(case) != row["expected_definition_sha256"]:
            raise ValueError("Assembly case correction input changed: " + case_id)
        changes = row["set"]
        execution_effect = row.get("execution_effect", "rescore_only")
        evidence = row.get("evidence")
        regression_ids = evidence.get("regression_ids") if isinstance(evidence, dict) else None
        if (
            not isinstance(changes, dict)
            or not changes
            or execution_effect not in {"rescore_only", "rerun_all_methods"}
            or set(changes) - (_CORRECTABLE_CASE_FIELDS | _RERUN_ONLY_CASE_FIELDS)
            or (set(changes) & _RERUN_ONLY_CASE_FIELDS and execution_effect != "rerun_all_methods")
            or not isinstance(evidence, dict)
            or not isinstance(evidence.get("source"), str)
            or not evidence["source"].strip()
            or not isinstance(evidence.get("observed_at"), str)
            or not evidence["observed_at"].strip()
            or not isinstance(evidence.get("observed_text"), str)
            or not evidence["observed_text"].strip()
            or not isinstance(regression_ids, list)
            or not regression_ids
            or any(
                not isinstance(identifier, str) or not identifier for identifier in regression_ids
            )
            or len(regression_ids) != len(set(regression_ids))
        ):
            raise ValueError("Assembly case correction lacks bounded fields or evidence")
        require_scoring_regressions(regression_ids)
        selected[case_id] = {**case, **changes}
        if execution_effect == "rerun_all_methods":
            rerun_all_methods.append(case_id)
    return rerun_all_methods


def assemble_profile_dataset(
    assembly_path: Path, *, repository: Path = ROOT, require_output_hash: bool = True
) -> tuple[dict, bytes]:
    """Build the exact preregistered case inventory without provider calls."""
    assembly_path = assembly_path.resolve()
    definition = json.loads(assembly_path.read_text(encoding="utf-8"))
    if (
        definition.get("schema_version") not in {1, 2}
        or definition.get("kind") != "profile_dataset_assembly"
        or not isinstance(definition.get("sources"), list)
    ):
        raise ValueError("Unsupported profile dataset assembly")
    if definition.get("case_corrections") is not None and definition["schema_version"] != 2:
        raise ValueError("Case corrections require profile dataset assembly schema 2")
    repository = repository.resolve()
    profile_path = contained_path(repository, definition["profile"], reject_symlinks=True)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    inventory = profile.get("case_inventory")
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("Assembly profile has no case inventory")
    if set(definition.get("dataset", {})) != {"id", "name", "tool", "dynamic_scoring"}:
        raise ValueError("Assembly dataset identity is incomplete")
    if definition["dataset"]["id"] != profile.get("dataset_id"):
        raise ValueError("Assembly dataset ID differs from its profile")

    selected: dict[str, dict] = {}
    input_sources = []
    owned_tracks: set[str] = set()
    for source in definition["sources"]:
        if set(source) != {"path", "sha256", "tracks"}:
            raise ValueError("Assembly source declaration is invalid")
        path = contained_path(repository, source["path"], reject_symlinks=True)
        raw = read_dataset_bytes(path)
        if _sha256(raw) != source["sha256"]:
            raise ValueError("Assembly source hash changed: " + source["path"])
        source_dataset = json.loads(raw)
        source_cases = {case["id"]: case for case in source_dataset.get("cases", [])}
        if len(source_cases) != len(source_dataset.get("cases", [])):
            raise ValueError("Assembly source has duplicate case IDs: " + source["path"])
        tracks = source["tracks"]
        if (
            not isinstance(tracks, list)
            or not tracks
            or any(track not in inventory for track in tracks)
            or owned_tracks.intersection(tracks)
        ):
            raise ValueError("Assembly source track ownership is invalid")
        owned_tracks.update(tracks)
        case_ids = [case_id for track in tracks for case_id in inventory[track]]
        missing = [case_id for case_id in case_ids if case_id not in source_cases]
        if missing:
            raise ValueError("Assembly source is missing profile cases: " + ", ".join(missing))
        for track in tracks:
            for case_id in inventory[track]:
                case = source_cases[case_id]
                if case.get("track", "web") != track or case_id in selected:
                    raise ValueError("Assembly case ownership differs from the profile: " + case_id)
                selected[case_id] = case
        input_sources.append(
            {
                "path": source["path"],
                "sha256": source["sha256"],
                "case_ids": case_ids,
            }
        )
    if owned_tracks != set(inventory):
        raise ValueError("Assembly sources do not own every profile track")

    rerun_all_methods = _apply_case_corrections(definition, selected, repository)

    ordered_ids = [case_id for cases in inventory.values() for case_id in cases]
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(selected):
        raise ValueError("Assembly profile case inventory is duplicated or incomplete")
    if len(ordered_ids) != profile.get("case_count"):
        raise ValueError("Assembly profile case count differs from its inventory")
    case_ids_sha256 = _sha256(
        json.dumps(ordered_ids, ensure_ascii=True, separators=(",", ":")).encode()
    )
    if case_ids_sha256 != profile.get("case_ids_sha256"):
        raise ValueError("Assembly profile case order changed")

    comparison = {
        "kind": profile["comparison_kind"],
        "timeout_seconds": profile["timeout_seconds"],
        "runs_per_case": profile["runs_per_case"],
        "concurrency": profile["concurrency"],
        "max_failure_retries": profile["max_failure_retries"],
        "require_independent_result_review": profile["require_independent_result_review"],
        "publication_state": "pre_call_frozen",
        "input_sources": input_sources,
        "reference_binding_mode": profile["reference_binding_mode"],
        "reproduction_profile": definition["profile"],
        "report_output": profile["report_output"],
    }
    if definition.get("case_corrections") is not None:
        comparison["case_corrections"] = {
            **definition["case_corrections"],
            "rerun_all_methods": rerun_all_methods,
        }
    dataset = {
        "schema_version": 1,
        **definition["dataset"],
        "cases": [selected[case_id] for case_id in ordered_ids],
        "comparison": comparison,
        "coverage_plan": {track: list(profile["providers"]) for track in inventory},
    }
    dataset = native_fetch_dataset(dataset)
    raw = _canonical_bytes(dataset)
    expected = definition.get("native_dataset_sha256")
    if require_output_hash and (
        not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        raise ValueError("Assembly has no valid native dataset hash")
    if expected is not None and _sha256(raw) != expected:
        raise ValueError("Assembled native dataset differs from its registered hash")
    return dataset, raw
