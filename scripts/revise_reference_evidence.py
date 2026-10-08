"""Correct frozen references and rescore immutable provider outputs offline.

This is intentionally narrower than a benchmark rerun: URLs, prompts,
acceptance contracts, call identity, output bytes and timing cannot change.
"""
import argparse
import hashlib
import json
import shutil
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.record_io import parse_jsonl
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.reference_lifecycle import requires_fresh_reference
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.scoring_regressions import (
    ROOT_CAUSE_CATEGORIES,
    build_correction_provenance,
)
from harness.web_fetch.rescoring import rescore
from harness.web_fetch.result_review import archive_superseded_result_review

ALLOWED_FIELDS = {
    "ground_truth_main_content", "source_verification", "body_requirements",
    "required_patterns", "reference_answer", "ground_truth_evidence",
}
SEPARATE_SCORING_FIELDS = {
    "required_facts", "acceptance_contract", "forbidden_any",
    "expected_resources", "resource_output_contract", "task_prompt",
    "retrieval_hints",
    "mutable_body_policy",
}
CAPTURE_INSTANCE_FIELDS = {
    "checked_at", "http_status", "source_snapshot_sha256",
    "extracted_reference_sha256", "browser_observation_sha256",
}
NON_SCORING_REFERENCE_FIELDS = {"ground_truth_evidence", "source_verification"}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def equivalent_source_verification(old: dict, new: dict) -> bool:
    """Ignore a fresh capture's identity when its frozen contract is unchanged."""
    left = {key: value for key, value in (old or {}).items()
            if key not in CAPTURE_INSTANCE_FIELDS}
    right = {key: value for key, value in (new or {}).items()
             if key not in CAPTURE_INSTANCE_FIELDS}
    return left == right


def reference_fields_for_case(old: dict, new: dict) -> set[str]:
    """Keep scoring-only body changes out of a reference correction."""
    answer_reference_changed = (
        old.get("ground_truth_main_content") != new.get("ground_truth_main_content")
        or not equivalent_source_verification(
            old.get("source_verification", {}), new.get("source_verification", {}))
    )
    return ALLOWED_FIELDS if answer_reference_changed else {
        "ground_truth_main_content", "source_verification", "ground_truth_evidence"
    }


def scoring_relevant_reference_change(changed: list[dict]) -> bool:
    """Provenance metadata changes do not justify rewriting measured rows."""
    return any(set(change["fields"]) - NON_SCORING_REFERENCE_FIELDS
               for change in changed)


def revise(source: Path, canonical_path: Path, *, root_cause_category: str,
           root_cause: str, regression_ids: list[str],
           case_ids: set[str] | None = None,
           fields: set[str] | None = None) -> dict:
    provenance = build_correction_provenance(
        root_cause_category, root_cause, regression_ids
    )
    source = source.resolve()
    dataset_path = contained_path(source, "dataset.json")
    results_path = contained_path(source, "results.jsonl")
    before_dataset = read_dataset_bytes(dataset_path)
    before_results = results_path.read_bytes()
    review_path = source / "result-review.json"
    if review_path.is_file():
        review = json.loads(review_path.read_text())
        if review.get("bindings") != {
                "dataset_sha256": digest(before_dataset),
                "results_sha256": digest(before_results)}:
            raise ValueError("Existing result review is not bound to reference revision inputs")
    dataset = json.loads(before_dataset)
    canonical = json.loads(read_dataset_bytes(canonical_path))
    old_cases = {case["id"]: case for case in dataset["cases"]}
    new_cases = {case["id"]: case for case in canonical["cases"]}
    if not old_cases.keys() <= new_cases.keys():
        raise ValueError("Measured evidence contains cases absent from the canonical dataset")
    if case_ids is not None and not case_ids <= old_cases.keys():
        raise ValueError("Reference revision names a case absent from measured evidence")
    if fields is not None and (not fields or not fields <= ALLOWED_FIELDS):
        raise ValueError("Reference revision has an unsupported field scope")
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
        reference_fields = reference_fields_for_case(old, new)
        if case_ids is not None and case_id not in case_ids:
            reference_fields = set()
        if fields is not None:
            reference_fields &= fields
        # A reference correction and an independently reviewed scoring-contract
        # correction may be pending at the same time. Preserve the old scoring
        # fields for the later scoring revision instead of either importing them
        # under the wrong provenance or blocking the reference-only step.
        forbidden = [key for key in set(old) | set(new)
                     if key not in ALLOWED_FIELDS | SEPARATE_SCORING_FIELDS
                     and old.get(key) != new.get(key)]
        if forbidden:
            raise ValueError(f"{case_id}: reference revision changed task fields {forbidden}")
        row = deepcopy(old)
        changed_fields = []
        for key in reference_fields:
            if (key == "source_verification"
                    and equivalent_source_verification(old.get(key, {}), new.get(key, {}))):
                continue
            if old.get(key) != new.get(key):
                changed_fields.append(key)
                if key in new:
                    row[key] = deepcopy(new[key])
                else:
                    row.pop(key, None)
        updated.append(row)
        if changed_fields:
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
                    "fields": sorted(changed_fields),
                    "old_reference_sha256": old_hash,
                    "new_reference_sha256": digest(new_reference.encode()),
                    "reason": "independent reference extraction/normalization correction",
                })
    dataset["cases"] = updated
    records = parse_jsonl(before_results)
    validate_report_ids(dataset, records)
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    # Keep the independently retained corrected source files beside the revised
    # dataset; provider artifacts remain byte-for-byte untouched.
    canonical_root = canonical_path.parent
    for folder in ("sources", "list-references"):
        incoming = canonical_root / folder
        if incoming.exists():
            shutil.copytree(incoming, source / folder, dirs_exist_ok=True)
    scoring_relevant_change = scoring_relevant_reference_change(changed)
    try:
        # Provenance labels are audit metadata, not scoring inputs. Rewriting all
        # result rows for such a correction would manufacture a scoring revision
        # and invalidate an otherwise unchanged result-review binding.
        decisions = (
            rescore(source, allow_rule_revision=True,
                    reference_revision_hashes=original_hashes)
            if scoring_relevant_change
            else []
        )
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
        "output_dataset_sha256": digest(read_dataset_bytes(dataset_path)),
        "output_results_sha256": digest(results_path.read_bytes()),
        "changed_cases": changed,
        "task_decision_changes": decisions,
        "provider_calls": 0,
        "immutable_call_fields": "attempt identity, output bytes/hash, timestamps and elapsed time retained",
        "correction_provenance": build_correction_provenance(
            provenance["category"], provenance["root_cause"],
            provenance["regression_ids"], decisions
        ),
        **(archive_superseded_result_review(
            review_path, before_dataset=before_dataset, before_results=before_results)
           if review_path.is_file() else {}),
    }
    receipt["revision_transitions"] = [{key: receipt[key] for key in (
        "input_dataset_sha256", "input_results_sha256",
        "output_dataset_sha256", "output_results_sha256",
    )}]
    receipt_path = source / "reference-revisions.json"
    history = json.loads(receipt_path.read_text()) if receipt_path.exists() else []
    if not isinstance(history, list):
        raise ValueError("Reference revision history must be a list")
    # Append-only history: a later reference correction is a new graph edge,
    # never a reason to rewrite an earlier receipt's endpoint.
    receipt_path.write_text(
        json.dumps([*history, receipt], ensure_ascii=False, indent=2) + "\n")
    # A reference correction is a separate, hash-bound transition.  Never
    # rewrite historical scoring outputs to the new reference result: doing so
    # destroys the chronological scoring chain.  The validator follows the
    # audited reference receipt as an explicit bridge before later scoring
    # revisions.
    scoring_path = source / "scoring-revisions.json"
    if scoring_path.exists() and not isinstance(json.loads(scoring_path.read_text()), list):
        raise ValueError("Scoring revision history must be a list")
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
    parser.add_argument("--case-id", action="append",
                        help="Restrict the audited revision to these case IDs; repeat as needed")
    parser.add_argument("--field", action="append", choices=sorted(ALLOWED_FIELDS),
                        help="Restrict the audited revision to these reference fields; repeat as needed")
    args = parser.parse_args()
    print(json.dumps(revise(
        args.source, args.canonical,
        root_cause_category=args.root_cause_category,
        root_cause=args.root_cause,
        regression_ids=args.regression_id,
        case_ids=set(args.case_id) if args.case_id else None,
        fields=set(args.field) if args.field else None,
    ), ensure_ascii=False, indent=2))
