#!/usr/bin/env python3
"""Assemble the resolved 100-case evidence without rerunning retained cases."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import task_passed
from harness.web_fetch.reference_lifecycle import reference_revision_cases
from harness.web_fetch.scoring_regressions import build_correction_provenance
from scripts.rescore_content import rescore


EXPECTED = {
    "base_dataset": "d8019897025bae95e9b6e5db9988d68f4052780d3b57fc933bcf9fb14c385cbc",
    "base_results": "f513cdb2dcc15679af732569a0f5f517786f19dfb1f23be0f03ee9e9e16803e9",
    "canonical_dataset": "ce24ba9bc36b83868b65b0fde0d8282d51319989a47925241ae9cb1d6016f390",
    "replacement_dataset": "169ccbafc68a705a9d084369f89f0c4ee1ee9e746224701b9bf73041d0040803",
    "replacement_results": "2384f44f0998307b2f3642a26e2100985f420bb278c60f4f8a59ca3bb760f291",
    "wrong_interface_dataset": "dfcd6f88c38744910db2c15be294fb0ec621f8e23f027b92e6430e19b2e63759",
    "wrong_interface_results": "69960ef67d26c396935ae9a7116be5828e7851678061eb49e223c6342880ff37",
    "failed_postcheck_dataset": "169ccbafc68a705a9d084369f89f0c4ee1ee9e746224701b9bf73041d0040803",
    "failed_postcheck_results": "a1d57f6737e809e4d5147182549b955438369487a46ef2facc9473430519ce4f",
}
OLD_ID = "dynamic-105-steam-city-search"
NEW_ID = "dynamic-105-blueprint-docs"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_checked(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    if digest(raw) != expected:
        raise ValueError(f"hash mismatch for {path}")
    return raw


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def rows(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def move_if_present(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)


def copy_tree_contents(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def retained_binding(records: list[dict], excluded: set[str]) -> str:
    bindings = sorted(
        (row["attempt_id"], row["case_id"], row["metrics"]["content_sha256"])
        for row in records if row["case_id"] not in excluded
    )
    return digest(json.dumps(bindings, separators=(",", ":")).encode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--replacement", type=Path, required=True)
    parser.add_argument("--wrong-interface", type=Path, required=True)
    parser.add_argument("--failed-postcheck", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")

    base_dataset_raw = read_checked(args.base / "dataset.json", EXPECTED["base_dataset"])
    base_results_raw = read_checked(args.base / "results.jsonl", EXPECTED["base_results"])
    canonical_raw = read_checked(args.canonical, EXPECTED["canonical_dataset"])
    replacement_dataset_raw = read_checked(
        args.replacement / "dataset.json", EXPECTED["replacement_dataset"])
    replacement_results_raw = read_checked(
        args.replacement / "results.jsonl", EXPECTED["replacement_results"])
    wrong_interface_dataset_raw = read_checked(
        args.wrong_interface / "dataset.json", EXPECTED["wrong_interface_dataset"])
    wrong_interface_results_raw = read_checked(
        args.wrong_interface / "results.jsonl", EXPECTED["wrong_interface_results"])
    failed_postcheck_dataset_raw = read_checked(
        args.failed_postcheck / "dataset.json", EXPECTED["failed_postcheck_dataset"])
    failed_postcheck_results_raw = read_checked(
        args.failed_postcheck / "results.jsonl", EXPECTED["failed_postcheck_results"])
    base_dataset = json.loads(base_dataset_raw)
    canonical = json.loads(canonical_raw)
    replacement_dataset = json.loads(replacement_dataset_raw)
    base_rows = rows(base_results_raw)
    replacement_rows = rows(replacement_results_raw)
    wrong_interface_rows = rows(wrong_interface_results_raw)
    failed_postcheck_rows = rows(failed_postcheck_results_raw)
    if (len(base_rows) != 100 or len(replacement_rows) != 1
            or len(wrong_interface_rows) != 1 or len(failed_postcheck_rows) != 1):
        raise ValueError("expected 100 retained-source rows and one replacement row")
    steam_rows = [row for row in base_rows if row["case_id"] == OLD_ID]
    if len(steam_rows) != 1 or replacement_rows[0]["case_id"] != NEW_ID:
        raise ValueError("case replacement identity mismatch")
    old_case = next(case for case in base_dataset["cases"] if case["id"] == OLD_ID)
    if {case["id"] for case in canonical["cases"]} != (
            {case["id"] for case in base_dataset["cases"]} - {OLD_ID} | {NEW_ID}):
        raise ValueError("canonical case inventory is not an exact one-for-one replacement")
    if len({case["url"] for case in canonical["cases"]}) != 100:
        raise ValueError("canonical dataset does not contain 100 unique URLs")

    revised_reference_hashes = reference_revision_cases(args.base)
    if not revised_reference_hashes:
        raise ValueError("base reference-revision lineage is unavailable")

    shutil.copytree(args.base, args.output)
    superseded = args.output / "superseded" / OLD_ID
    move_if_present(
        args.output / "artifacts" / "moli-webfetch" / OLD_ID,
        superseded / "artifacts" / "moli-webfetch" / OLD_ID,
    )
    move_if_present(args.output / "sources" / OLD_ID, superseded / "sources" / OLD_ID)
    (superseded / "result.json").write_bytes(json_bytes(steam_rows[0]))
    (superseded / "case.json").write_bytes(json_bytes(old_case))
    for name in ("artifact-site-receipt.json", "artifact-site-deployment-verification.json"):
        move_if_present(args.output / name, args.output / "superseded" / "prior-publication" / name)
    move_if_present(
        args.output / "result-audit.json",
        args.output / "superseded" / "prior-audit" / "result-audit.json",
    )

    superseded_measurements = []
    for source, label, dataset_raw, results_raw, reason in (
        (args.wrong_interface, "dynamic-105-blueprint-docs-wrong-interface",
         wrong_interface_dataset_raw, wrong_interface_results_raw,
         "current selected-main request route was not comparable to the original legacy Moli interface"),
        (args.failed_postcheck, "dynamic-105-blueprint-docs-failed-postcheck",
         failed_postcheck_dataset_raw, failed_postcheck_results_raw,
         "legacy request route was correct, but the postcheck rebuilt a newer scope identity and the immutable certificate failed"),
    ):
        row = rows(results_raw)[0]
        if row.get("case_id") != NEW_ID:
            raise ValueError("superseded replacement identity mismatch")
        preserved = args.output / "superseded" / label
        shutil.copytree(source, preserved)
        superseded_measurements.append({
            "case_id": NEW_ID,
            "attempt_id": row["attempt_id"],
            "artifact_sha256": row["metrics"]["content_sha256"],
            "measurement_dataset_sha256": digest(dataset_raw),
            "measurement_results_sha256": digest(results_raw),
            "reason": reason,
            "preserved_under": f"superseded/{label}",
        })

    copy_tree_contents(args.replacement / "artifacts", args.output / "artifacts")
    copy_tree_contents(args.replacement / "call-logs", args.output / "call-logs")
    copy_tree_contents(args.replacement / "sources", args.output / "sources")
    copy_tree_contents(
        args.replacement / "reference-checks", args.output / "reference-checks")

    assembled_rows = [row for row in base_rows if row["case_id"] != OLD_ID] + replacement_rows
    order = {case["id"]: index for index, case in enumerate(canonical["cases"])}
    assembled_rows.sort(key=lambda row: order[row["case_id"]])
    if len(assembled_rows) != 100 or len({row["attempt_id"] for row in assembled_rows}) != 100:
        raise ValueError("assembled logical result inventory is incomplete")
    args.output.joinpath("dataset.json").write_bytes(canonical_raw)
    assembled_results_raw = b"".join(
        json.dumps(row, ensure_ascii=True).encode() + b"\n" for row in assembled_rows)
    args.output.joinpath("results.jsonl").write_bytes(assembled_results_raw)

    base_execution = json.loads((args.base / "execution.json").read_text())
    replacement_execution = json.loads((args.replacement / "execution.json").read_text())
    old_attempt = steam_rows[0]["attempt_id"]
    found = 0
    for batch in base_execution["batches"]:
        attempt_ids = batch.get("attempt_ids", [])
        if old_attempt in attempt_ids:
            found += 1
            batch["attempt_ids"] = [value for value in attempt_ids if value != old_attempt]
            batch["attempt_count"] = len(batch["attempt_ids"])
            checks = batch.get("reference_checks", {}).get("cases", {})
            checks.pop(OLD_ID, None)
    if found != 1:
        raise ValueError("Steam attempt did not have exactly one execution owner")
    batches = base_execution["batches"] + replacement_execution["batches"]
    completed = max(batch["completed_at"] for batch in batches)
    started = min(batch["started_at"] for batch in batches)
    execution = {
        "schema_version": 1,
        "aggregate": {
            "started_at": started,
            "completed_at": completed,
            "providers": ["moli-webfetch"],
            "case_ids": [case["id"] for case in canonical["cases"]],
            "batch_count": len(batches),
            "authoritative_logical_attempts": 100,
            "physical_calls_in_lineage": 103,
            "retained_calls": 99,
            "superseded_calls": 3,
            "replacement_calls_attempted": 3,
            "authoritative_replacement_calls": 1,
        },
        "batches": batches,
    }
    args.output.joinpath("execution.json").write_bytes(json_bytes(execution))

    assembly_dataset_hash = digest(canonical_raw)
    assembly_results_hash = digest(assembled_results_raw)
    replacement_receipt = {
        "schema_version": 1,
        "kind": "audited_case_replacement_assembly",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": EXPECTED["base_dataset"],
        "input_results_sha256": EXPECTED["base_results"],
        "output_dataset_sha256": assembly_dataset_hash,
        "output_results_sha256": assembly_results_hash,
        "provider_calls": 3,
        "retained_attempts": 99,
        "superseded_attempt": {
            "case_id": OLD_ID,
            "attempt_id": old_attempt,
            "artifact_sha256": steam_rows[0]["metrics"]["content_sha256"],
            "reason": "complete declared Steam target was already present in initial HTTP",
            "preserved_under": "superseded/dynamic-105-steam-city-search",
        },
        "replacement_attempt": {
            "case_id": NEW_ID,
            "attempt_id": replacement_rows[0]["attempt_id"],
            "artifact_sha256": replacement_rows[0]["metrics"]["content_sha256"],
            "measurement_dataset_sha256": EXPECTED["replacement_dataset"],
            "measurement_results_sha256": EXPECTED["replacement_results"],
        },
        "superseded_replacement_attempts": superseded_measurements,
        "retained_artifact_bindings_sha256": retained_binding(base_rows, {OLD_ID}),
        "selection_policy": "one-for-one eligibility correction; no score-based selection",
    }
    args.output.joinpath("case-replacement.json").write_bytes(json_bytes(replacement_receipt))

    before_rescore_dataset = args.output.joinpath("dataset.json").read_bytes()
    before_rescore_results = args.output.joinpath("results.jsonl").read_bytes()
    decisions = rescore(
        args.output, allow_rule_revision=True,
        reference_revision_hashes=revised_reference_hashes,
    )
    scoring = json.loads((args.output / "scoring-revisions.json").read_text())
    scoring.append({
        "schema_version": 2,
        "kind": "audited_scoring_rule_revision",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": digest(before_rescore_dataset),
        "input_results_sha256": digest(before_rescore_results),
        "output_dataset_sha256": digest((args.output / "dataset.json").read_bytes()),
        "output_results_sha256": digest((args.output / "results.jsonl").read_bytes()),
        "changed_cases": [
            {"case_id": "dynamic-react-cart", "fields": ["forbidden_any"]},
            {"case_id": NEW_ID, "fields": ["forbidden_any"]},
        ],
        "task_decision_changes": decisions,
        "provider_calls": 0,
        "immutable_call_fields": "attempt identity, output bytes/hash, timestamps and elapsed time retained",
        "correction_provenance": build_correction_provenance(
            "multi-root",
            "Two declared-scope rules were encoded incorrectly: React Cart rejected a record-attached control, while Blueprint did not enforce its explicit top-bar and sidebar exclusion.",
            ["complete-records-allow-supplemental-ui",
             "supplemental-ui-cannot-repair-incomplete-records",
             "reference-scope-excludes-site-chrome"],
            decisions,
        ),
    })
    args.output.joinpath("scoring-revisions.json").write_bytes(json_bytes(scoring))

    final_results = rows((args.output / "results.jsonl").read_bytes())
    final_cases = {case["id"]: case for case in canonical["cases"]}
    passed = sum(task_passed(row["ok"], row["metrics"],
                             final_cases[row["case_id"]].get("task_kind", "body"))
                 for row in final_results)
    result_audit = {
        "schema_version": 1,
        "attempts": 100,
        "unique_attempts": len({row["attempt_id"] for row in final_results}),
        "unique_cases": len({row["case_id"] for row in final_results}),
        "unique_urls": len({row["url"] for row in final_results}),
        "providers": sorted({row["provider"] for row in final_results}),
        "task_pass": passed,
        "task_fail": 100 - passed,
        "independent_review_status": "required_after_policy_and_case-inventory change",
        "provider_calls_added": 3,
    }
    args.output.joinpath("result-audit.json").write_bytes(json_bytes(result_audit))

    prior_lineage = json.loads((args.base / "measurement-lineage.json").read_text())
    lineage = {
        "schema_version": 1,
        "kind": "dynamic_100_formal_audit_resolution",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "authoritative_dataset_sha256": digest((args.output / "dataset.json").read_bytes()),
        "authoritative_results_sha256": digest((args.output / "results.jsonl").read_bytes()),
        "logical_attempts": 100,
        "physical_calls_in_lineage": 103,
        "retained_original_calls": 99,
        "superseded_original_calls": 1,
        "replacement_calls": 3,
        "superseded_replacement_calls": 2,
        "authoritative_replacement_calls": 1,
        "offline_rescore_calls": 0,
        "case_replacement_receipt_sha256": digest(
            (args.output / "case-replacement.json").read_bytes()),
        "prior_lineage": prior_lineage,
    }
    args.output.joinpath("measurement-lineage.json").write_bytes(json_bytes(lineage))
    print(json.dumps({
        "dataset_sha256": lineage["authoritative_dataset_sha256"],
        "results_sha256": lineage["authoritative_results_sha256"],
        "task_pass": passed,
        "task_fail": 100 - passed,
        "rescore_decisions": decisions,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
