"""Apply reviewed body/table scoring rules to saved outputs without provider calls."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.reference_lifecycle import reference_revision_cases
from harness.web_fetch.result_review import review_input_identity
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.scoring_regressions import (
    ROOT_CAUSE_CATEGORIES, build_correction_provenance,
)
from scripts.rescore_content import rescore

ALLOWED_FIELDS = {
    'body_requirements', 'required_patterns', 'required_facts',
    'acceptance_contract', 'forbidden_any', 'expected_resources',
    'resource_output_contract', 'task_prompt',
    'mutable_body_policy',
}
class IndependentReviewRequired(ValueError):
    """The prior review is valid history but cannot certify revised scores."""


def correction_provenance(category, root_cause, regression_ids, decisions=()):
    """Require an actionable cause and executable controls for every score revision."""
    return build_correction_provenance(category, root_cause, regression_ids, decisions)


def _without_rules(case):
    return {key:value for key,value in case.items() if key not in ALLOWED_FIELDS}


def _resource_prompt_policy_correction(old: dict, new: dict) -> bool:
    """Allow wording to follow one narrowly audited resource-policy correction.

    The physical resource request receives the URL and native resource route;
    it never receives a target URL.  Changing an impossible target-only
    precision requirement to recall acceptance therefore changes scoring and
    its reader-facing wording, not the preserved provider call.
    """
    before = old.get('resource_output_contract', {})
    after = new.get('resource_output_contract', {})
    old_targets = old.get('expected_resources', [])
    new_targets = new.get('expected_resources', [])
    aliases_expand_only = (
        len(old_targets) == len(new_targets)
        and all(
            prior.get('id') == current.get('id')
            and prior.get('kind') == current.get('kind')
            and prior.get('url_patterns', []) == current.get('url_patterns', [])
            and set(prior.get('urls', [])) <= set(current.get('urls', []))
            for prior, current in zip(old_targets, new_targets)
        )
    )
    return (
        old.get('task_kind', 'body') == new.get('task_kind', 'body') == 'resource'
        and aliases_expand_only
        and before.get('candidate_scope') == after.get('candidate_scope') == 'image_answer_fields'
        and before.get('unexpected') == 'reject'
        and after.get('unexpected') == 'allow'
        and {key: value for key, value in before.items() if key != 'unexpected'}
            == {key: value for key, value in after.items() if key != 'unexpected'}
    )


def rebind_unchanged_result_review(path, before_dataset, before_results,
                                   after_dataset, after_results, *, rebound_at):
    """Rebind a blind review only when its actual review inputs are unchanged."""
    if not path.is_file():
        return False
    review = json.loads(path.read_text())
    old_dataset_hash = hashlib.sha256(before_dataset).hexdigest()
    old_results_hash = hashlib.sha256(before_results).hexdigest()
    if review.get('bindings') != {
            'dataset_sha256': old_dataset_hash,
            'results_sha256': old_results_hash}:
        raise ValueError('Existing result review is not bound to scoring inputs')
    before_document = json.loads(before_dataset)
    after_document = json.loads(after_dataset)
    review_inputs = ('cases', 'coverage_plan')
    changed_review_inputs = [key for key in review_inputs
                             if before_document.get(key) != after_document.get(key)]
    if changed_review_inputs:
        raise IndependentReviewRequired(
            'Dataset review inputs changed; independent result review must be rerun: '
            + ', '.join(changed_review_inputs))
    allowed_metadata = {'id', 'name', 'comparison'}
    changed_top_level = sorted(
        key for key in set(before_document) | set(after_document)
        if before_document.get(key) != after_document.get(key)
    )
    if set(changed_top_level) - allowed_metadata:
        raise IndependentReviewRequired(
            'Dataset changed outside non-judgmental metadata; independent result review must be rerun')
    before_comparison = before_document.get('comparison', {})
    after_comparison = after_document.get('comparison', {})
    changed_comparison = {
        key for key in set(before_comparison) | set(after_comparison)
        if before_comparison.get(key) != after_comparison.get(key)
    }
    if changed_comparison - {'conversion'}:
        raise IndependentReviewRequired(
            'Comparison semantics changed; independent result review must be rerun: '
            + ', '.join(sorted(changed_comparison - {'conversion'}))
        )
    old_rows = {row['attempt_id']: row for row in map(json.loads, before_results.splitlines())}
    new_rows = {row['attempt_id']: row for row in map(json.loads, after_results.splitlines())}
    if old_rows.keys() != new_rows.keys():
        raise IndependentReviewRequired(
            'Attempt inventory changed; independent result review must be rerun')
    cases = {case['id']: case for case in after_document['cases']}
    scope = review.get('review_protocol', {}).get('scope', 'all')
    reviewed_ids = {row['attempt_id'] for row in review.get('attempts', [])}
    expected = set()
    for attempt_id, old in old_rows.items():
        new = new_rows[attempt_id]
        if review_input_identity(old) != review_input_identity(new):
            raise IndependentReviewRequired(
                'Review-visible input changed; independent result review must be rerun')
        kind = cases[new['case_id']].get('task_kind', 'body')
        old_verdict = task_passed(old['ok'], old['metrics'], kind)
        new_verdict = task_passed(new['ok'], new['metrics'], kind)
        if old_verdict != new_verdict:
            raise IndependentReviewRequired(
                'Machine verdict changed; independent result review must be rerun')
        if scope == 'all' or not new_verdict:
            expected.add(attempt_id)
    if reviewed_ids != expected:
        raise IndependentReviewRequired(
            'Review scope changed; independent result review must be rerun')
    machine = {row['attempt_id']: row for row in review['attempts']}
    for attempt_id in expected:
        verdict = 'pass' if task_passed(
            new_rows[attempt_id]['ok'], new_rows[attempt_id]['metrics'],
            cases[new_rows[attempt_id]['case_id']].get('task_kind', 'body')) else 'fail'
        if machine[attempt_id].get('machine_verdict') != verdict:
            raise IndependentReviewRequired(
                'Reviewed machine verdict changed; independent result review must be rerun')
    review['bindings'] = {
        'dataset_sha256': hashlib.sha256(after_dataset).hexdigest(),
        'results_sha256': hashlib.sha256(after_results).hexdigest(),
    }
    review.setdefault('review_protocol', {})['rebound_after_nonjudgmental_metadata_change'] = {
        'at': rebound_at, 'prior_results_sha256': old_results_hash,
        'prior_dataset_sha256': old_dataset_hash,
        'changed_top_level_fields': changed_top_level,
        'provider_calls': 0,
        'condition': 'cases, coverage, review-visible inputs, artifact hashes, scope and machine verdicts unchanged',
    }
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2)+'\n')
    return True


def archive_superseded_result_review(path: Path, *, before_dataset: bytes,
                                     before_results: bytes) -> dict:
    """Move a valid superseded review into immutable, hash-addressed history."""
    review_bytes = path.read_bytes()
    review = json.loads(review_bytes)
    old_dataset_hash = hashlib.sha256(before_dataset).hexdigest()
    old_results_hash = hashlib.sha256(before_results).hexdigest()
    if review.get('bindings') != {
            'dataset_sha256': old_dataset_hash,
            'results_sha256': old_results_hash}:
        raise ValueError('Existing result review is not bound to scoring inputs')
    history = path.parent/'review-history'
    history.mkdir(exist_ok=True)
    archive = history/f'result-review-{old_dataset_hash[:12]}-{old_results_hash[:12]}.json'
    if archive.exists():
        if archive.read_bytes() != review_bytes:
            raise ValueError(f'Review history collision: {archive.name}')
        path.unlink()
    else:
        path.replace(archive)
    return {
        'prior_result_review_path': archive.relative_to(path.parent).as_posix(),
        'prior_result_review_sha256': hashlib.sha256(review_bytes).hexdigest(),
        'independent_review_required': True,
    }


def _revise_unlocked(source: Path, canonical_path: Path, *, root_cause_category: str,
                     root_cause: str, regression_ids: list[str]):
    # Validate correction intent and executable controls before touching evidence.
    provenance = correction_provenance(
        root_cause_category, root_cause, regression_ids
    )
    source = source.resolve()
    dataset_path = contained_path(source, 'dataset.json')
    results_path = contained_path(source, 'results.jsonl')
    before_dataset = dataset_path.read_bytes()
    before_results = results_path.read_bytes()
    review_path = source/'result-review.json'
    if review_path.is_file():
        review = json.loads(review_path.read_text())
        if review.get('bindings') != {
                'dataset_sha256': hashlib.sha256(before_dataset).hexdigest(),
                'results_sha256': hashlib.sha256(before_results).hexdigest()}:
            raise ValueError('Existing result review is not bound to scoring inputs')
    # Validate and retain any preceding reference correction before changing
    # the dataset hash.  The rescore then checks both revisions against the
    # original live-call bindings.
    revised_reference_hashes = reference_revision_cases(source)
    reference_receipt_path = source/'reference-revisions.json'
    prior_reference_bytes = (reference_receipt_path.read_bytes()
                             if reference_receipt_path.is_file() else None)
    prior_reference_receipts = (json.loads(reference_receipt_path.read_text())
                                if revised_reference_hashes else None)
    dataset = json.loads(before_dataset)
    canonical = json.loads(canonical_path.read_text())
    old_cases = {case['id']:case for case in dataset['cases']}
    new_cases = {case['id']:case for case in canonical['cases']}
    if not old_cases.keys() <= new_cases.keys():
        raise ValueError('Measured evidence contains cases absent from canonical dataset')
    changed = []
    updated_cases = {}
    for identifier, old in old_cases.items():
        canonical_case = new_cases[identifier]
        # Scoring and reference corrections are independent audit dimensions.
        # Keep the measured cohort's frozen reference bytes for this step; a
        # later reference revision may update them under separate provenance.
        for key in ('url','track','task_kind'):
            if old.get(key) != canonical_case.get(key):
                raise ValueError(identifier + ': captured task differs from canonical ' + key)
        if (old.get('task_prompt') != canonical_case.get('task_prompt')
                and not _resource_prompt_policy_correction(old, canonical_case)):
            raise ValueError(identifier + ': task_prompt changed outside the audited resource-policy correction')
        new = dict(old)
        for key in ALLOWED_FIELDS:
            if key in canonical_case:new[key]=canonical_case[key]
            else:new.pop(key,None)
        updated_cases[identifier]=new
        fields = sorted(key for key in ALLOWED_FIELDS if old.get(key) != new.get(key))
        if fields:
            changed.append({'case_id':identifier, 'fields':fields})
    dataset['cases'] = [updated_cases[case['id']] for case in dataset['cases']]
    validate_report_ids(dataset, [json.loads(line) for line in before_results.splitlines()])
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2)+'\n')
    staged_dataset = dataset_path.read_bytes()
    try:
        decisions = rescore(source, allow_rule_revision=True,
                            reference_revision_hashes=revised_reference_hashes or None)
    except BaseException:
        # Never roll stale bytes over a different process's completed revision.
        # Standard callers are serialized below; this comparison also protects
        # against an uncooperative writer that does not take the lock.
        if (dataset_path.read_bytes() == staged_dataset
                and results_path.read_bytes() == before_results):
            dataset_path.write_bytes(before_dataset)
            results_path.write_bytes(before_results)
        raise
    # A shared scorer implementation can change a verdict without changing a
    # case field. Record those cases too so the review merger re-audits every
    # method for the affected task instead of silently reusing old judgments.
    attempt_cases = {row['attempt_id']: row['case_id']
                     for row in map(json.loads, before_results.splitlines())}
    changed_by_case = {row['case_id']: row for row in changed}
    for decision in decisions:
        case_id = attempt_cases[decision['attempt_id']]
        changed_by_case.setdefault(case_id, {'case_id': case_id, 'fields': []})
    changed = [changed_by_case[case_id] for case_id in sorted(changed_by_case)]
    receipt_path = source/'scoring-revisions.json'
    history = json.loads(receipt_path.read_text()) if receipt_path.exists() else []
    review_revision = {}
    try:
        rebind_unchanged_result_review(
            review_path, before_dataset, before_results,
            dataset_path.read_bytes(), results_path.read_bytes(),
            rebound_at=datetime.now(timezone.utc).isoformat())
    except IndependentReviewRequired:
        review_revision = archive_superseded_result_review(
            review_path, before_dataset=before_dataset,
            before_results=before_results)
    receipt = {
        'schema_version':2,
        'kind':'audited_scoring_rule_revision',
        'scored_at':datetime.now(timezone.utc).isoformat(),
        'input_dataset_sha256':hashlib.sha256(before_dataset).hexdigest(),
        'input_results_sha256':hashlib.sha256(before_results).hexdigest(),
        'output_dataset_sha256':hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        'output_results_sha256':hashlib.sha256(results_path.read_bytes()).hexdigest(),
        'changed_cases':changed,
        'task_decision_changes':decisions,
        'provider_calls':0,
        'immutable_call_fields':'attempt identity, output bytes/hash, timestamps and elapsed time retained',
        'correction_provenance': correction_provenance(
            provenance['category'], provenance['root_cause'],
            provenance['regression_ids'], decisions
        ),
        **review_revision,
    }
    history.append(receipt)
    receipt_path.write_text(json.dumps(history, ensure_ascii=False, indent=2)+'\n')
    # Reference receipts are immutable history.  Their output can remain an
    # ancestor of the current bundle; lifecycle validation follows the later
    # scoring edge instead of rebinding the historical receipt in place.
    if prior_reference_receipts is not None:
        assert reference_receipt_path.read_bytes() == prior_reference_bytes
    return receipt


def revise(source: Path, canonical_path: Path, *, root_cause_category: str,
           root_cause: str, regression_ids: list[str]):
    """Serialize evidence revisions and make rollback ownership explicit."""
    source = source.resolve()
    lock_id = hashlib.sha256(str(source).encode()).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / f"browser-eval-scoring-revision-{lock_id}.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _revise_unlocked(
                source, canonical_path,
                root_cause_category=root_cause_category,
                root_cause=root_cause,
                regression_ids=regression_ids,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--canonical',type=Path,default=ROOT/'evaluation/dataset.json')
    parser.add_argument('--root-cause-category',required=True,
                        choices=sorted(ROOT_CAUSE_CATEGORIES))
    parser.add_argument('--root-cause',required=True,
                        help='Concrete defect mechanism, not the observed score change')
    parser.add_argument('--regression-id',action='append',required=True,
                        help='Executable case id from evaluation/scoring-regressions.json; repeat as needed')
    args=parser.parse_args()
    print(json.dumps(revise(
        args.source, args.canonical,
        root_cause_category=args.root_cause_category,
        root_cause=args.root_cause,
        regression_ids=args.regression_id,
    ),ensure_ascii=False,indent=2))
