"""Correct frozen references and rescore immutable provider outputs offline.

This is intentionally narrower than a benchmark rerun: URLs, prompts,
acceptance contracts, call identity, output bytes and timing cannot change.
"""

import argparse
import hashlib
import json
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from harness.record_io import parse_jsonl
from harness.web_fetch.reference_inputs import (
    input_tree,
    read_dataset_bytes,
    require_mutable_inputs,
)
from harness.web_fetch.reference_lifecycle import (
    requires_fresh_reference,
    valid_reference_revision,
    valid_scoring_revision,
)
from harness.web_fetch.rescoring import rescore
from harness.web_fetch.result_review import archive_superseded_result_review
from harness.web_fetch.safety import commit_staged_evidence, evidence_tree_identity, reject_evidence_symlinks, validate_report_ids
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.scoring_regressions import ROOT_CAUSE_CATEGORIES, build_correction_provenance

ROOT = Path(__file__).resolve().parents[2]

ALLOWED_FIELDS = {
    "ground_truth_main_content",
    "source_verification",
    "body_requirements",
    "required_patterns",
    "reference_answer",
    "ground_truth_evidence",
}
SEPARATE_SCORING_FIELDS = {
    "required_facts",
    "acceptance_contract",
    "forbidden_any",
    "expected_resources",
    "resource_output_contract",
    "task_prompt",
    "retrieval_hints",
    "mutable_body_policy",
}
CAPTURE_INSTANCE_FIELDS = {
    "checked_at",
    "http_status",
    "source_snapshot_sha256",
    "extracted_reference_sha256",
    "browser_observation_sha256",
}
NON_SCORING_REFERENCE_FIELDS = {"ground_truth_evidence", "source_verification"}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def equivalent_source_verification(old: dict, new: dict) -> bool:
    """Ignore a fresh capture's identity when its frozen contract is unchanged."""
    left = {key: value for key, value in (old or {}).items() if key not in CAPTURE_INSTANCE_FIELDS}
    right = {key: value for key, value in (new or {}).items() if key not in CAPTURE_INSTANCE_FIELDS}
    return left == right


def reference_fields_for_case(old: dict, new: dict) -> set[str]:
    """Keep scoring-only body changes out of a reference correction."""
    answer_reference_changed = old.get("ground_truth_main_content") != new.get(
        "ground_truth_main_content"
    ) or not equivalent_source_verification(
        old.get("source_verification", {}), new.get("source_verification", {})
    )
    return (
        set(ALLOWED_FIELDS)
        if answer_reference_changed
        else {"ground_truth_main_content", "source_verification", "ground_truth_evidence"}
    )


def scoring_relevant_reference_change(changed: list[dict]) -> bool:
    """Provenance metadata changes do not justify rewriting measured rows."""
    return any(set(change["fields"]) - NON_SCORING_REFERENCE_FIELDS for change in changed)


def _revise_staged(
    source: Path,
    canonical_path: Path,
    *,
    root_cause_category: str,
    root_cause: str,
    regression_ids: list[str],
    case_ids: set[str] | None = None,
    fields: set[str] | None = None,
) -> dict:
    provenance = build_correction_provenance(root_cause_category, root_cause, regression_ids)
    source = source.resolve()
    dataset_path = contained_path(source, "dataset.json")
    results_path = contained_path(source, "results.jsonl")
    before_dataset = read_dataset_bytes(dataset_path)
    before_results = results_path.read_bytes()
    receipt_path = source / "reference-revisions.json"
    history = json.loads(receipt_path.read_bytes()) if receipt_path.exists() else []
    review_path = source / "result-review.json"
    if review_path.is_file():
        review = json.loads(review_path.read_text())
        if review.get("bindings") != {
            "dataset_sha256": digest(before_dataset),
            "results_sha256": digest(before_results),
        }:
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
    batches = (
        json.loads(execution_path.read_text())["batches"]
        if execution_path.is_file()
        else [json.loads((source / "manifest.json").read_text())]
    )
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
        forbidden = [
            key
            for key in set(old) | set(new)
            if key not in ALLOWED_FIELDS | SEPARATE_SCORING_FIELDS and old.get(key) != new.get(key)
        ]
        if forbidden:
            raise ValueError(f"{case_id}: reference revision changed task fields {forbidden}")
        row = deepcopy(old)
        changed_fields = []
        for key in reference_fields:
            if key == "source_verification" and equivalent_source_verification(
                old.get(key, {}), new.get(key, {})
            ):
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
                if verification.get("state") != "valid" or verification.get(
                    "reference_sha256"
                ) != digest(new_reference.encode()):
                    raise ValueError(
                        f"{case_id}: corrected reference lacks valid independent evidence"
                    )
            hashes = original_hashes.get(case_id) or {
                digest(old.get("ground_truth_main_content", "").encode())
            }
            for old_hash in sorted(hashes):
                changed.append(
                    {
                        "case_id": case_id,
                        "fields": sorted(changed_fields),
                        "old_reference_sha256": old_hash,
                        "new_reference_sha256": digest(new_reference.encode()),
                        "reason": "independent reference extraction/normalization correction",
                    }
                )
    if not changed:
        raise ValueError("Reference revision has no selected changes")
    dataset["cases"] = updated
    records = parse_jsonl(before_results)
    validate_report_ids(dataset, records)
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    # Keep the independently retained corrected source files beside the revised
    # dataset; provider artifacts remain byte-for-byte untouched.
    selected = {
        item["case_id"] for item in changed if set(item["fields"]) - {"ground_truth_evidence"}
    }
    for folder in ("sources", "list-references"):
        incoming = input_tree(canonical_path, folder)
        for identifier in selected:
            relative = identifier if folder == "sources" else identifier + ".json"
            origin = contained_path(incoming, relative, reject_symlinks=True)
            target = contained_path(source / folder, relative, reject_symlinks=True)
            if origin.is_dir():
                expected = evidence_tree_identity(origin)
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(origin, target)
                if evidence_tree_identity(target) != expected:
                    raise ValueError("Corrected reference changed during copy: " + identifier)
            elif origin.is_file():
                expected = digest(origin.read_bytes())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target)
                if digest(target.read_bytes()) != expected:
                    raise ValueError("Corrected reference changed during copy: " + identifier)
    scoring_relevant_change = scoring_relevant_reference_change(changed)
    try:
        # Provenance labels are audit metadata, not scoring inputs. Rewriting all
        # result rows for such a correction would manufacture a scoring revision
        # and invalidate an otherwise unchanged result-review binding.
        decisions = (
            rescore(source, case_ids=selected, allow_rule_revision=True,
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
        "immutable_call_fields": (
            "attempt identity, output bytes/hash, timestamps and elapsed time retained"
        ),
        "correction_provenance": build_correction_provenance(
            provenance["category"],
            provenance["root_cause"],
            provenance["regression_ids"],
            decisions,
        ),
        **(
            archive_superseded_result_review(
                review_path, before_dataset=before_dataset, before_results=before_results
            )
            if review_path.is_file()
            else {}
        ),
    }
    receipt["revision_transitions"] = [
        {
            key: receipt[key]
            for key in (
                "input_dataset_sha256",
                "input_results_sha256",
                "output_dataset_sha256",
                "output_results_sha256",
            )
        }
    ]
    # Append-only history: a later reference correction is a new graph edge,
    # never a reason to rewrite an earlier receipt's endpoint.
    receipt_path.write_text(json.dumps([*history, receipt], ensure_ascii=False, indent=2) + "\n")
    # A reference correction is a separate, hash-bound transition.  Never
    # rewrite historical scoring outputs to the new reference result: doing so
    # destroys the chronological scoring chain.  The validator follows the
    # audited reference receipt as an explicit bridge before later scoring
    # revisions.
    return receipt


def revise(
    source: Path,
    canonical_path: Path,
    *,
    root_cause_category: str,
    root_cause: str,
    regression_ids: list[str],
    case_ids: set[str] | None = None,
    fields: set[str] | None = None,
) -> dict:
    """Publish a complete reference correction, or preserve the original bundle."""
    from harness.record_io import load_jsonl
    from harness.web_fetch.artifacts import verify_record_artifacts

    reject_evidence_symlinks(source)
    source = source.resolve()
    require_mutable_inputs(source / "dataset.json")
    build_correction_provenance(root_cause_category, root_cause, regression_ids)
    for name, validator in (
        ("reference-revisions.json", valid_reference_revision),
        ("scoring-revisions.json", valid_scoring_revision),
    ):
        path = source / name
        if path.exists():
            rows = json.loads(path.read_bytes())
            if (
                not isinstance(rows, list)
                or any(not isinstance(row, dict) or not row for row in rows)
                or (rows and not validator(source))
            ):
                raise ValueError("Invalid or unbound revision history: " + name)
    from .native_routes import native_cases_by_id
    native_cases = native_cases_by_id(source)
    for record in load_jsonl(source / "results.jsonl"):
        verify_record_artifacts(source, record, native_cases=native_cases)
    canonical_bytes = read_dataset_bytes(canonical_path)
    before_identity = evidence_tree_identity(source)
    for folder in ("sources", "list-references"):
        input_tree(canonical_path, folder)
    work = Path(tempfile.mkdtemp(prefix=f".{source.name}-reference-history-", dir=source.parent))
    staged, backup = work / "staged", work / "original"
    committed = False
    try:
        shutil.copytree(source, staged)
        receipt = _revise_staged(
            staged,
            canonical_path,
            root_cause_category=root_cause_category,
            root_cause=root_cause,
            regression_ids=regression_ids,
            case_ids=case_ids,
            fields=fields,
        )
        if evidence_tree_identity(source) != before_identity:
            raise ValueError("Reference revision inputs changed during staging")
        if read_dataset_bytes(canonical_path) != canonical_bytes:
            raise ValueError("Canonical reference changed during staging")
        native_cases = native_cases_by_id(staged)
        for record in load_jsonl(staged / "results.jsonl"):
            verify_record_artifacts(staged, record, native_cases=native_cases)
        commit_staged_evidence(source, staged, backup)
        committed = True
        return receipt
    finally:
        # Successful revisions retain the complete predecessor privately, including
        # original reference files. Failed recovery also keeps its sole original.
        if not committed and not backup.exists():
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument(
        "--root-cause-category", required=True, choices=sorted(ROOT_CAUSE_CATEGORIES)
    )
    parser.add_argument(
        "--root-cause",
        required=True,
        help="Concrete defect mechanism, not the observed score change",
    )
    parser.add_argument(
        "--regression-id",
        action="append",
        required=True,
        help="Executable case id from evaluation/scoring-regressions.json; repeat as needed",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        help="Restrict the audited revision to these case IDs; repeat as needed",
    )
    parser.add_argument(
        "--field",
        action="append",
        choices=sorted(ALLOWED_FIELDS),
        help="Restrict the audited revision to these reference fields; repeat as needed",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            revise(
                args.source,
                args.canonical,
                root_cause_category=args.root_cause_category,
                root_cause=args.root_cause,
                regression_ids=args.regression_id,
                case_ids=set(args.case_id) if args.case_id else None,
                fields=set(args.field) if args.field else None,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
