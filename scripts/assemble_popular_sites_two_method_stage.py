#!/usr/bin/env python3
# ruff: noqa: E402 -- repository imports follow the explicit path setup below
"""Assemble the authorized Moli/Lexmount popular-sites stage without new calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import summarize
from harness.web_fetch.safety import contained_path
from scripts.rescore_content import rescore

PROVIDERS = ("moli-webfetch", "lexmount-global")
REPLACED_CASES = {"challenge-02", "challenge-04", "challenge-06"}
PRE_CALL_DATASET_SHA256 = "b8907677ef1850de1de22188f8923cfa97f9a7511e652b9d339c54eca9efd07b"
EXPECTED_DATASET_SHA256 = "43932d1602cdc9b54598fe22149d5940e4371d2eb39501f5b65f5d05246e35fb"
EXPECTED_RULES_SHA256 = "3a564f6f019734a75607223b9ca3987e88bdc0ad0d8b6e530b6f0c2b1a03e26b"
EXPECTED_OLD_RESULTS_SHA256 = "c8714e9ae97021619c7f01f62ece684d8c65a662ff05ff83c89c2d9fffc12b7b"
EXECUTION_CONTRACT = "research/popular-sites-two-method-execution-contract-2026-09-15.json"
CORRECTION_RECEIPT = "research/popular-sites-rules-v2-post-freeze-correction-2026-09-15.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text().splitlines()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def batches(path: Path) -> list[dict]:
    return json.loads((path / "execution.json").read_text())["batches"]


def select_batch(path: Path, provider: str) -> dict:
    matches = [
        batch
        for batch in batches(path)
        if any(item.get("name") == provider for item in batch.get("providers", []))
    ]
    if len(matches) != 1:
        raise ValueError(f"{path}: expected one execution batch for {provider}")
    return matches[0]


def projected_batch(path: Path, provider: str, attempt_ids: set[str], role: str) -> dict:
    source = path / "execution.json"
    batch = deepcopy(select_batch(path, provider))
    batch["attempt_ids"] = [item for item in batch["attempt_ids"] if item in attempt_ids]
    batch["attempt_count"] = len(batch["attempt_ids"])
    checks = batch.get("reference_checks", {}).get("cases")
    if isinstance(checks, dict):
        wanted_cases = {item.split("__", 2)[1] for item in batch["attempt_ids"]}
        batch["reference_checks"]["cases"] = {
            key: value for key, value in checks.items() if key in wanted_cases
        }
    batch.get("environment", {}).pop("hostname", None)
    batch["projection"] = {
        "kind": "popular-sites-two-method-stage",
        "role": role,
        "source_execution": str(source),
        "source_execution_sha256": digest(source),
        "selected_attempt_count": len(attempt_ids),
        "selection_basis": "predeclared case scope, never result quality",
    }
    return batch


def verify_artifact(root: Path, row: dict) -> None:
    artifact = contained_path(root, row["artifact_path"])
    if digest(artifact) != row["metrics"]["content_sha256"]:
        raise ValueError("Artifact hash mismatch: " + row["attempt_id"])


def declared_paths(value: object, prefix: str):
    if isinstance(value, dict):
        for item in value.values():
            yield from declared_paths(item, prefix)
    elif isinstance(value, list):
        for item in value:
            yield from declared_paths(item, prefix)
    elif isinstance(value, str) and value.startswith(prefix + "/"):
        yield Path(*Path(value).parts[1:])


def copy_canonical_reference_tree(dataset: dict, output: Path) -> None:
    """Copy exactly the reference files declared by the current dataset."""
    for folder in ("sources", "list-references"):
        source_root = ROOT / "evaluation" / folder
        wanted = set(declared_paths(dataset, folder))
        if folder == "sources":
            wanted |= {
                path.parent / "evidence.json"
                for path in wanted
                if (source_root / path.parent / "evidence.json").is_file()
            }
        else:
            wanted |= {
                Path(case["id"] + ".json")
                for case in dataset.get("cases", [])
                if not case.get("acceptance_contract")
                and case.get("source_benchmark", {}).get("mode")
                == "dynamic_list_structure"
                and case.get("source_benchmark", {}).get("verification_scope")
                == "first_three_name_detail_pairs"
            }
        for relative in sorted(wanted):
            source = source_root / relative
            if not source.is_file():
                raise ValueError(f"Missing canonical reference file: {folder}/{relative}")
            target = output / folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--moli-new", type=Path, required=True)
    parser.add_argument("--lexmount-new", type=Path, required=True)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "evaluation/popular-sites-dataset.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old, moli_new, lex_new = (
        args.old.resolve(),
        args.moli_new.resolve(),
        args.lexmount_new.resolve(),
    )
    output, dataset_path = args.output.resolve(), args.dataset.resolve()
    if output.exists():
        parser.error("Output exists; refusing to overwrite evidence")
    if digest(dataset_path) != EXPECTED_DATASET_SHA256:
        raise ValueError("Frozen dataset hash changed")
    if digest(ROOT / "research/popular-sites-rules.json") != EXPECTED_RULES_SHA256:
        raise ValueError("Frozen rule registry hash changed")
    if digest(old / "results.jsonl") != EXPECTED_OLD_RESULTS_SHA256:
        raise ValueError("Prior 800-result authority changed")

    dataset = json.loads(dataset_path.read_text())
    cases = dataset["cases"]
    case_ids = [case["id"] for case in cases]
    if len(case_ids) != 100 or len(case_ids) != len(set(case_ids)):
        raise ValueError("Expected 100 unique frozen cases")
    case_by_id = {case["id"]: case for case in cases}

    old_moli = {
        row["case_id"]: row
        for row in read_rows(old)
        if row["provider"] == "moli-webfetch" and row["case_id"] not in REPLACED_CASES
    }
    new_moli = {row["case_id"]: row for row in read_rows(moli_new)}
    new_lex = {row["case_id"]: row for row in read_rows(lex_new)}
    if set(old_moli) != set(case_ids) - REPLACED_CASES:
        raise ValueError("Inherited Moli scope is not the exact 97 unchanged-URL cases")
    if set(new_moli) != REPLACED_CASES:
        raise ValueError("New Moli scope is not the exact three changed-URL cases")
    if set(new_lex) != set(case_ids):
        raise ValueError("Lexmount scope is not the exact 100 cases")
    if {row["provider"] for row in new_moli.values()} != {"moli-webfetch"}:
        raise ValueError("Unexpected provider in new Moli evidence")
    if {row["provider"] for row in new_lex.values()} != {"lexmount-global"}:
        raise ValueError("Unexpected provider in new Lexmount evidence")

    selected: list[tuple[dict, Path, str]] = []
    for case_id in case_ids:
        selected.append(
            (
                new_moli.get(case_id) or old_moli[case_id],
                moli_new if case_id in REPLACED_CASES else old,
                "new" if case_id in REPLACED_CASES else "inherited",
            )
        )
        selected.append((new_lex[case_id], lex_new, "new"))
    for row, source, _ in selected:
        if row["url"] != case_by_id[row["case_id"]]["url"]:
            raise ValueError("Result URL differs from frozen case: " + row["attempt_id"])
        verify_artifact(source, row)
    if len({row["attempt_id"] for row, _, _ in selected}) != 200:
        raise ValueError("Expected 200 unique authoritative attempt IDs")

    output.mkdir(parents=True)
    copy_canonical_reference_tree(dataset, output)
    for provider in PROVIDERS:
        (output / "artifacts" / provider).mkdir(parents=True)
    rows = []
    for row, source, _ in selected:
        target = output / row["artifact_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / row["artifact_path"], target)
        rows.append(deepcopy(row))

    dataset = deepcopy(dataset)
    dataset["coverage_plan"] = {"challenge": list(PROVIDERS)}
    dataset["comparison"].update(
        {
            "publication_state": "measured_pending_independent_review",
            "stage_label": "2-method audited stage",
            "execution_contract": EXECUTION_CONTRACT,
            "execution_contract_sha256": digest(ROOT / EXECUTION_CONTRACT),
            "fresh_formal_result_count": 103,
            "inherited_formal_result_count": 97,
            "provider_results_reused": 97,
            "required_new_provider_calls": 103,
            "score_only_recompute_count": 97,
            "deferred_providers": [],
            "post_freeze_correction": {
                "path": CORRECTION_RECEIPT,
                "sha256": digest(ROOT / CORRECTION_RECEIPT),
                "provider_calls": 0,
                "stable_text_changed": False,
            },
        }
    )
    write_json(output / "dataset.json", dataset)
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows)
    )

    inherited_ids = {row["attempt_id"] for row, _, role in selected if role == "inherited"}
    moli_new_ids = {row["attempt_id"] for row in new_moli.values()}
    lex_new_ids = {row["attempt_id"] for row in new_lex.values()}
    execution_batches = [
        projected_batch(old, "moli-webfetch", inherited_ids, "inherited_97_unchanged_urls"),
        projected_batch(moli_new, "moli-webfetch", moli_new_ids, "new_3_changed_urls"),
        projected_batch(
            lex_new, "lexmount-global", lex_new_ids, "new_100_updated_hosted_deployment"
        ),
    ]
    write_json(
        output / "execution.json",
        {
            "batches": execution_batches,
            "assembly": {
                "new_provider_calls": 103,
                "inherited_provider_calls": 97,
                "authoritative_logical_results": 200,
                "selection_policy": "Predeclared scope only; no retries or best-result selection.",
            },
        },
    )

    input_hash = digest(output / "results.jsonl")
    changes = rescore(output, allow_rule_revision=True)
    result_hash = digest(output / "results.jsonl")
    rescored = read_rows(output)
    write_json(output / "summary.json", summarize(rescored))
    write_json(
        output / "scoring-revisions.json",
        [
            {
                "kind": "popular_sites_rules_v2_offline_rescore",
                "scored_at": datetime.now(UTC).isoformat(),
                "pre_call_dataset_sha256": PRE_CALL_DATASET_SHA256,
                "corrected_rule_dataset_sha256": EXPECTED_DATASET_SHA256,
                "post_freeze_correction_sha256": digest(ROOT / CORRECTION_RECEIPT),
                "evidence_dataset_sha256": digest(output / "dataset.json"),
                "input_results_sha256": input_hash,
                "output_results_sha256": result_hash,
                "task_decision_changes": changes,
                "scope": "all 200 selected outputs scored once under the frozen rules v2 contract",
                "provider_calls": 0,
                "provider_output_used_as_rule_source": False,
            }
        ],
    )
    provider_infos = [
        next(
            item
            for item in select_batch(moli_new, "moli-webfetch")["providers"]
            if item["name"] == "moli-webfetch"
        ),
        next(
            item
            for item in select_batch(lex_new, "lexmount-global")["providers"]
            if item["name"] == "lexmount-global"
        ),
    ]
    write_json(
        output / "manifest.json",
        {
            "schema_version": 1,
            "dataset_sha256": digest(output / "dataset.json"),
            "tool": dataset.get("tool"),
            "started_at": min(row["started_at"] for row in rescored),
            "completed_at": max(row["completed_at"] for row in rescored),
            "providers": provider_infos,
            "attempt_count": 200,
            "attempt_ids": [row["attempt_id"] for row in rescored],
            "unexecuted": [],
            "derived_from": {
                "old_800_results_sha256": EXPECTED_OLD_RESULTS_SHA256,
                "moli_new_results_sha256": digest(moli_new / "results.jsonl"),
                "lexmount_new_results_sha256": digest(lex_new / "results.jsonl"),
                "post_freeze_correction_sha256": digest(ROOT / CORRECTION_RECEIPT),
                "reason": "97 predeclared Moli inheritances plus 103 predeclared new calls",
            },
        },
    )
    write_json(
        output / "call-accounting.json",
        {
            "actual_new_physical_calls": 103,
            "moli_webfetch_new_calls": 3,
            "lexmount_global_new_calls": 100,
            "inherited_moli_calls": 97,
            "retries": 0,
            "authoritative_results": 200,
        },
    )
    print(
        json.dumps(
            {
                "results": len(rescored),
                "new_calls": 103,
                "inherited": 97,
                "rescoring_decision_changes": len(changes),
                "dataset_sha256": digest(output / "dataset.json"),
                "results_sha256": result_hash,
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
