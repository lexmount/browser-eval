"""Correct frozen references and rescore immutable provider outputs offline.

This is intentionally narrower than a benchmark rerun: URLs, prompts,
acceptance contracts, call identity, output bytes and timing cannot change.
"""
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
from harness.web_fetch.reference_lifecycle import requires_fresh_reference
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.scoring_regressions import (
    ROOT_CAUSE_CATEGORIES, build_correction_provenance,
)
from scripts.rescore_content import rescore

ALLOWED_FIELDS = {
    "ground_truth_main_content", "source_verification", "body_requirements",
    "required_patterns", "reference_answer",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def revise(source: Path, canonical_path: Path, *, root_cause_category: str,
           root_cause: str, regression_ids: list[str]) -> dict:
    provenance = build_correction_provenance(
        root_cause_category, root_cause, regression_ids
    )
    source = source.resolve()
    dataset_path = contained_path(source, "dataset.json")
    results_path = contained_path(source, "results.jsonl")
    before_dataset = dataset_path.read_bytes()
    before_results = results_path.read_bytes()
    dataset = json.loads(before_dataset)
    canonical = json.loads(canonical_path.read_text())
    old_cases = {case["id"]: case for case in dataset["cases"]}
    new_cases = {case["id"]: case for case in canonical["cases"]}
    if old_cases.keys() != new_cases.keys():
        raise ValueError("Reference revision cannot change the task inventory")
    changed = []
    original_hashes = {}
    execution_path = source / "execution.json"
    batches = (json.loads(execution_path.read_text())["batches"] if execution_path.is_file()
               else [json.loads((source / "manifest.json").read_text())])
    for batch in batches:
        for case_id, binding in batch.get("reference_checks", {}).get("cases", {}).items():
            original_hashes.setdefault(case_id, set()).add(binding["reference_sha256"])
    updated = []
    for case_id, old in old_cases.items():
        new = new_cases[case_id]
        forbidden = [key for key in set(old) | set(new)
                     if key not in ALLOWED_FIELDS and old.get(key) != new.get(key)]
        if forbidden:
            raise ValueError(f"{case_id}: reference revision changed task fields {forbidden}")
        row = deepcopy(old)
        fields = []
        for key in ALLOWED_FIELDS:
            if old.get(key) != new.get(key):
                fields.append(key)
                if key in new:
                    row[key] = deepcopy(new[key])
                else:
                    row.pop(key, None)
        updated.append(row)
        if fields:
            new_reference = new.get("ground_truth_main_content", "")
            verification = new.get("source_verification", {})
            if requires_fresh_reference(new):
                if (verification.get("state") != "valid"
                        or verification.get("reference_sha256") != digest(new_reference.encode())):
                    raise ValueError(f"{case_id}: corrected reference lacks valid independent evidence")
            hashes = original_hashes.get(case_id) or {
                digest(old.get("ground_truth_main_content", "").encode())}
            for old_hash in sorted(hashes):
                changed.append({
                    "case_id": case_id,
                    "fields": sorted(fields),
                    "old_reference_sha256": old_hash,
                    "new_reference_sha256": digest(new_reference.encode()),
                    "reason": "independent reference extraction/normalization correction",
                })
    dataset["cases"] = updated
    records = [json.loads(line) for line in before_results.splitlines()]
    validate_report_ids(dataset, records)
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    # Keep the independently retained corrected source files beside the revised
    # dataset; provider artifacts remain byte-for-byte untouched.
    canonical_root = canonical_path.parent
    for folder in ("sources", "list-references"):
        incoming = canonical_root / folder
        if incoming.exists():
            shutil.copytree(incoming, source / folder, dirs_exist_ok=True)
    try:
        decisions = rescore(source, allow_rule_revision=True,
                            reference_revision_hashes=original_hashes)
    except BaseException:
        dataset_path.write_bytes(before_dataset)
        results_path.write_bytes(before_results)
        raise
    receipt = {
        "schema_version": 2,
        "kind": "audited_reference_correction",
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": digest(before_dataset),
        "input_results_sha256": digest(before_results),
        "output_dataset_sha256": digest(dataset_path.read_bytes()),
        "output_results_sha256": digest(results_path.read_bytes()),
        "changed_cases": changed,
        "task_decision_changes": decisions,
        "provider_calls": 0,
        "immutable_call_fields": "attempt identity, output bytes/hash, timestamps and elapsed time retained",
        "correction_provenance": build_correction_provenance(
            provenance["category"], provenance["root_cause"],
            provenance["regression_ids"], decisions
        ),
    }
    (source / "reference-revisions.json").write_text(
        json.dumps([receipt], ensure_ascii=False, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--root-cause-category", required=True,
                        choices=sorted(ROOT_CAUSE_CATEGORIES))
    parser.add_argument("--root-cause", required=True,
                        help="Concrete defect mechanism, not the observed score change")
    parser.add_argument("--regression-id", action="append", required=True,
                        help="Executable case id from evaluation/scoring-regressions.json; repeat as needed")
    args = parser.parse_args()
    print(json.dumps(revise(
        args.source, args.canonical,
        root_cause_category=args.root_cause_category,
        root_cause=args.root_cause,
        regression_ids=args.regression_id,
    ), ensure_ascii=False, indent=2))
