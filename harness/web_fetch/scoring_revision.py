"""Apply reviewed body/table scoring rules to saved outputs without provider calls."""

import argparse
import fcntl
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from harness.record_io import parse_jsonl
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.reference_lifecycle import (
    HOMEPAGE_READER_SCOPE_PROMPTS,
    reference_revision_cases,
    valid_scoring_revision,
)
from harness.web_fetch.rescoring import rescore
from harness.web_fetch.result_review import (
    IndependentReviewRequired,
    archive_superseded_result_review,
    rebind_unchanged_result_review,
)
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
from harness.web_fetch.scoring_regressions import (
    ROOT_CAUSE_CATEGORIES,
    build_correction_provenance,
)

ALLOWED_FIELDS = {
    "body_requirements",
    "required_patterns",
    "required_facts",
    "acceptance_contract",
    "composite_acceptance",
    "forbidden_any",
    "expected_resources",
    "resource_output_contract",
    "task_prompt",
    "reference_answer",
    "mutable_body_policy",
}


def correction_provenance(category, root_cause, regression_ids, decisions=()):
    """Require an actionable cause and executable controls for every score revision."""
    return build_correction_provenance(category, root_cause, regression_ids, decisions)


def _without_rules(case):
    return {key: value for key, value in case.items() if key not in ALLOWED_FIELDS}


def _resource_prompt_policy_correction(old: dict, new: dict) -> bool:
    """Allow wording to follow one narrowly audited resource-policy correction."""
    before = old.get("resource_output_contract", {})
    after = new.get("resource_output_contract", {})
    old_targets = old.get("expected_resources", [])
    new_targets = new.get("expected_resources", [])
    aliases_expand_only = len(old_targets) == len(new_targets) and all(
        prior.get("id") == current.get("id")
        and prior.get("kind") == current.get("kind")
        and prior.get("url_patterns", []) == current.get("url_patterns", [])
        and set(prior.get("urls", [])) <= set(current.get("urls", []))
        for prior, current in zip(old_targets, new_targets)
    )
    return (
        old.get("task_kind", "body") == new.get("task_kind", "body") == "resource"
        and aliases_expand_only
        and before.get("candidate_scope") == after.get("candidate_scope") == "image_answer_fields"
        and before.get("unexpected") == "reject"
        and after.get("unexpected") == "allow"
        and {key: value for key, value in before.items() if key != "unexpected"}
        == {key: value for key, value in after.items() if key != "unexpected"}
    )


def _homepage_prompt_anchor_correction(old: dict, new: dict) -> bool:
    """Allow a homepage scope description to stop embedding revised answer anchors."""
    before = old.get("acceptance_contract", {})
    after = new.get("acceptance_contract", {})
    without_anchors = lambda value: {
        key: item for key, item in value.items() if key != "stable_text_anchors"
    }
    anchors = after.get("stable_text_anchors", [])
    return (
        old.get("source_benchmark", {}).get("mode")
        == new.get("source_benchmark", {}).get("mode")
        == "ranked_domain_homepage"
        and before.get("stable_text_anchors") != anchors
        and without_anchors(before) == without_anchors(after)
        and bool(anchors)
        and new.get("task_prompt") in HOMEPAGE_READER_SCOPE_PROMPTS
    )


def _scoring_source_sha256s(records: list[dict], *, label: str) -> list[str]:
    values = {
        row.get("metrics", {}).get("scoring_source_sha256")
        for row in records
    }
    if (not values or any(not isinstance(value, str) or len(value) != 64
                          or any(char not in "0123456789abcdef" for char in value)
                          for value in values)):
        raise ValueError(label + " results require valid scoring source SHA-256 values")
    return sorted(values)


def _revise_unlocked(
    source: Path,
    canonical_path: Path,
    *,
    root_cause_category: str,
    root_cause: str,
    regression_ids: list[str],
    validate_only: bool = False,
):
    # Validate correction intent and executable controls before touching evidence.
    provenance = correction_provenance(root_cause_category, root_cause, regression_ids)
    source = source.resolve()
    dataset_path = contained_path(source, "dataset.json")
    results_path = contained_path(source, "results.jsonl")
    before_dataset = read_dataset_bytes(dataset_path)
    before_results = results_path.read_bytes()
    before_records = parse_jsonl(before_results)
    input_scoring_source_sha256s = _scoring_source_sha256s(
        before_records, label="Input",
    )
    receipt_path = contained_path(source, "scoring-revisions.json", reject_symlinks=True)
    history = json.loads(receipt_path.read_bytes()) if receipt_path.exists() else []
    if not isinstance(history, list) or (history and not valid_scoring_revision(source)):
        raise ValueError(
            "Existing scoring revision history is invalid or not bound to current evidence"
        )
    review_path = source / "result-review.json"
    if review_path.is_file():
        review = json.loads(review_path.read_text())
        if review.get("bindings") != {
            "dataset_sha256": hashlib.sha256(before_dataset).hexdigest(),
            "results_sha256": hashlib.sha256(before_results).hexdigest(),
        }:
            raise ValueError("Existing result review is not bound to scoring inputs")
    # Validate and retain any preceding reference correction before changing
    # the dataset hash.  The rescore then checks both revisions against the
    # original live-call bindings.
    revised_reference_hashes = reference_revision_cases(source)
    reference_receipt_path = source / "reference-revisions.json"
    prior_reference_bytes = (
        reference_receipt_path.read_bytes() if reference_receipt_path.is_file() else None
    )
    prior_reference_receipts = (
        json.loads(reference_receipt_path.read_text()) if revised_reference_hashes else None
    )
    dataset = json.loads(before_dataset)
    canonical = json.loads(read_dataset_bytes(canonical_path))
    old_cases = {case["id"]: case for case in dataset["cases"]}
    new_cases = {case["id"]: case for case in canonical["cases"]}
    if not old_cases.keys() <= new_cases.keys():
        raise ValueError("Measured evidence contains cases absent from canonical dataset")
    changed = []
    updated_cases = {}
    for identifier, old in old_cases.items():
        canonical_case = new_cases[identifier]
        # Scoring and reference corrections are independent audit dimensions.
        # Keep the measured cohort's frozen reference bytes for this step; a
        # later reference revision may update them under separate provenance.
        for key in ("url", "track", "task_kind"):
            if old.get(key) != canonical_case.get(key):
                raise ValueError(identifier + ": captured task differs from canonical " + key)
        if old.get("task_prompt") != canonical_case.get("task_prompt") and not (
            _resource_prompt_policy_correction(old, canonical_case)
            or _homepage_prompt_anchor_correction(old, canonical_case)
        ):
            raise ValueError(
                identifier + ": task_prompt changed without its audited scoring contract"
            )
        new = dict(old)
        for key in ALLOWED_FIELDS:
            if key in canonical_case:
                new[key] = canonical_case[key]
            else:
                new.pop(key, None)
        updated_cases[identifier] = new
        fields = sorted(key for key in ALLOWED_FIELDS if old.get(key) != new.get(key))
        if fields:
            changed.append({"case_id": identifier, "fields": fields})
    dataset["cases"] = [updated_cases[case["id"]] for case in dataset["cases"]]
    validate_report_ids(dataset, before_records)
    if input_scoring_source_sha256s != [SCORING_SOURCE_SHA256] and changed:
        raise ValueError(
            "Shared scorer and task requirements changed together; "
            "apply a scorer-only revision first"
        )
    if validate_only and changed:
        raise ValueError('Scoring validation cannot change task requirements')
    if not validate_only:
        dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    staged_dataset = read_dataset_bytes(dataset_path)
    try:
        decisions = rescore(
            source,
            case_ids={change["case_id"] for change in changed} if changed else None,
            allow_rule_revision=True,
            reference_revision_hashes=revised_reference_hashes or None,
            validate_only=validate_only,
        )
    except BaseException:
        # Never roll stale bytes over a different process's completed revision.
        # Standard callers are serialized below; this comparison also protects
        # against an uncooperative writer that does not take the lock.
        if (
            read_dataset_bytes(dataset_path) == staged_dataset
            and results_path.read_bytes() == before_results
        ):
            dataset_path.write_bytes(before_dataset)
            results_path.write_bytes(before_results)
        raise
    # A shared scorer implementation can change a verdict without changing a
    # case field. Record those cases too so the review merger re-audits every
    # method for the affected task instead of silently reusing old judgments.
    attempt_cases = {row["attempt_id"]: row["case_id"] for row in parse_jsonl(before_results)}
    changed_by_case = {row["case_id"]: row for row in changed}
    for decision in decisions:
        case_id = attempt_cases[decision["attempt_id"]]
        changed_by_case.setdefault(case_id, {"case_id": case_id, "fields": []})
    changed = [changed_by_case[case_id] for case_id in sorted(changed_by_case)]
    output_scoring_source_sha256s = _scoring_source_sha256s(
        parse_jsonl(results_path.read_bytes()), label="Output",
    )
    if output_scoring_source_sha256s != [SCORING_SOURCE_SHA256]:
        raise ValueError("Scoring revision did not bind the current shared scorer")
    review_revision = {}
    try:
        rebind_unchanged_result_review(
            review_path,
            before_dataset,
            before_results,
            read_dataset_bytes(dataset_path),
            results_path.read_bytes(),
            rebound_at=datetime.now(timezone.utc).isoformat(),
        )
    except IndependentReviewRequired:
        review_revision = archive_superseded_result_review(
            review_path, before_dataset=before_dataset, before_results=before_results
        )
    receipt = {
        "schema_version": 2,
        "kind": "audited_scoring_rule_revision",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": hashlib.sha256(before_dataset).hexdigest(),
        "input_results_sha256": hashlib.sha256(before_results).hexdigest(),
        "output_dataset_sha256": hashlib.sha256(read_dataset_bytes(dataset_path)).hexdigest(),
        "output_results_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
        **(
            {"input_scoring_source_sha256": input_scoring_source_sha256s[0]}
            if len(input_scoring_source_sha256s) == 1
            else {"input_scoring_source_sha256s": input_scoring_source_sha256s}
        ),
        "output_scoring_source_sha256": output_scoring_source_sha256s[0],
        "changed_cases": changed,
        "task_decision_changes": decisions,
        "provider_calls": 0,
        **({'validation_only': True} if validate_only else {}),
        "immutable_call_fields": (
            "attempt identity, output bytes/hash, timestamps and elapsed time retained"
        ),
        "correction_provenance": correction_provenance(
            provenance["category"],
            provenance["root_cause"],
            provenance["regression_ids"],
            decisions,
        ),
        **review_revision,
    }
    history.append(receipt)
    receipt_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")
    # Reference receipts are immutable history.  Their output can remain an
    # ancestor of the current bundle; lifecycle validation follows the later
    # scoring edge instead of rebinding the historical receipt in place.
    if prior_reference_receipts is not None:
        assert reference_receipt_path.read_bytes() == prior_reference_bytes
    return receipt


def rebind_report_interpretation(source: Path, binding: dict, *, implementation_binding: dict | None = None):
    """Bind presentation content while preserving the scored output bytes."""
    from .reference_lifecycle import valid_scoring_revision
    source = source.resolve()
    if not valid_scoring_revision(source):
        raise ValueError('Report binding requires a valid existing scoring chain')
    if set(binding) != {'path', 'sha256'}:
        raise ValueError('Invalid report interpretation binding')
    asset = contained_path(source, binding['path'], reject_symlinks=True)
    if hashlib.sha256(asset.read_bytes()).hexdigest() != binding['sha256']:
        raise ValueError('Report interpretation content hash mismatch')
    if implementation_binding is not None:
        if (set(implementation_binding) != {'path', 'sha256'}
                or implementation_binding['path'] != 'implementation-provenance.json'):
            raise ValueError('Invalid implementation provenance binding')
        implementation = contained_path(source, implementation_binding['path'], reject_symlinks=True)
        if hashlib.sha256(implementation.read_bytes()).hexdigest() != implementation_binding['sha256']:
            raise ValueError('Implementation provenance content hash mismatch')
    dataset_path = contained_path(source, 'dataset.json')
    before = dataset_path.read_bytes()
    dataset = json.loads(before)
    dataset.setdefault('comparison', {})['report_interpretation'] = binding
    if implementation_binding is not None:
        dataset['comparison']['implementation_provenance'] = implementation_binding
    after = (json.dumps(dataset, ensure_ascii=False, indent=2)+'\n').encode()
    results_hash = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
    receipt = {'kind': 'report_interpretation_binding_revision', 'provider_calls': 0,
               'input_results_sha256': results_hash, 'output_results_sha256': results_hash}
    receipt['bound_asset_fields'] = ['report_interpretation'] + (
        ['implementation_provenance'] if implementation_binding is not None else [])
    for side, raw in (('input', before), ('output', after)):
        digest = hashlib.sha256(raw).hexdigest()
        relative = f'revision-history/dataset-{digest}.json'
        target = contained_path(source, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('Dataset history collision')
        target.write_bytes(raw)
        receipt[f'{side}_dataset_sha256'] = digest
        receipt[f'{side}_dataset_path'] = relative
    path = source/'report-binding-revisions.json'
    history = json.loads(path.read_text()) if path.exists() else []
    history.append(receipt)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2)+'\n')
    dataset_path.write_bytes(after)
    return receipt


def revise(
    source: Path,
    canonical_path: Path,
    *,
    root_cause_category: str,
    root_cause: str,
    regression_ids: list[str],
    validate_only: bool = False,
):
    """Serialize evidence revisions and make rollback ownership explicit."""
    source = source.resolve()
    lock_id = hashlib.sha256(str(source).encode()).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / f"browser-eval-scoring-revision-{lock_id}.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _revise_unlocked(
                source,
                canonical_path,
                root_cause_category=root_cause_category,
                root_cause=root_cause,
                regression_ids=regression_ids,
                validate_only=validate_only,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


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
    args = parser.parse_args(argv)
    print(
        json.dumps(
            revise(
                args.source,
                args.canonical,
                root_cause_category=args.root_cause_category,
                root_cause=args.root_cause,
                regression_ids=args.regression_id,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
