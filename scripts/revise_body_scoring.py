"""Apply reviewed body/table scoring rules to saved outputs without provider calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.reference_lifecycle import reference_revision_cases
from harness.web_fetch.result_review import review_input_identity
from harness.web_fetch.safety import contained_path, validate_report_ids
from scripts.rescore_content import rescore

ALLOWED_FIELDS = {
    'body_requirements', 'required_patterns', 'required_facts',
    'acceptance_contract',
}


def _without_rules(case):
    return {key:value for key,value in case.items() if key not in ALLOWED_FIELDS}


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
        raise ValueError(
            'Dataset review inputs changed; independent result review must be rerun: '
            + ', '.join(changed_review_inputs))
    allowed_metadata = {'id', 'name', 'comparison'}
    changed_top_level = sorted(
        key for key in set(before_document) | set(after_document)
        if before_document.get(key) != after_document.get(key)
    )
    if set(changed_top_level) - allowed_metadata:
        raise ValueError('Dataset changed outside non-judgmental metadata; independent result review must be rerun')
    before_comparison = before_document.get('comparison', {})
    after_comparison = after_document.get('comparison', {})
    changed_comparison = {
        key for key in set(before_comparison) | set(after_comparison)
        if before_comparison.get(key) != after_comparison.get(key)
    }
    if changed_comparison - {'conversion'}:
        raise ValueError(
            'Comparison semantics changed; independent result review must be rerun: '
            + ', '.join(sorted(changed_comparison - {'conversion'}))
        )
    old_rows = {row['attempt_id']: row for row in map(json.loads, before_results.splitlines())}
    new_rows = {row['attempt_id']: row for row in map(json.loads, after_results.splitlines())}
    if old_rows.keys() != new_rows.keys():
        raise ValueError('Attempt inventory changed; independent result review must be rerun')
    cases = {case['id']: case for case in after_document['cases']}
    scope = review.get('review_protocol', {}).get('scope', 'all')
    reviewed_ids = {row['attempt_id'] for row in review.get('attempts', [])}
    expected = set()
    for attempt_id, old in old_rows.items():
        new = new_rows[attempt_id]
        if review_input_identity(old) != review_input_identity(new):
            raise ValueError('Review-visible input changed; independent result review must be rerun')
        kind = cases[new['case_id']].get('task_kind', 'body')
        old_verdict = task_passed(old['ok'], old['metrics'], kind)
        new_verdict = task_passed(new['ok'], new['metrics'], kind)
        if old_verdict != new_verdict:
            raise ValueError('Machine verdict changed; independent result review must be rerun')
        if scope == 'all' or not new_verdict:
            expected.add(attempt_id)
    if reviewed_ids != expected:
        raise ValueError('Review scope changed; independent result review must be rerun')
    machine = {row['attempt_id']: row for row in review['attempts']}
    for attempt_id in expected:
        verdict = 'pass' if task_passed(
            new_rows[attempt_id]['ok'], new_rows[attempt_id]['metrics'],
            cases[new_rows[attempt_id]['case_id']].get('task_kind', 'body')) else 'fail'
        if machine[attempt_id].get('machine_verdict') != verdict:
            raise ValueError('Reviewed machine verdict changed; independent result review must be rerun')
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


def revise(source: Path, canonical_path: Path):
    source = source.resolve()
    dataset_path = contained_path(source, 'dataset.json')
    results_path = contained_path(source, 'results.jsonl')
    before_dataset = dataset_path.read_bytes()
    before_results = results_path.read_bytes()
    review_path = source/'result-review.json'
    # Validate and retain any preceding reference correction before changing
    # the dataset hash.  The rescore then checks both revisions against the
    # original live-call bindings.
    revised_reference_hashes = reference_revision_cases(source)
    reference_receipt_path = source/'reference-revisions.json'
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
        for key in ('url','track','task_kind','task_prompt','ground_truth_main_content'):
            if old.get(key) != canonical_case.get(key):
                raise ValueError(identifier + ': captured task/reference differs from canonical ' + key)
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
    try:
        decisions = rescore(source, allow_rule_revision=True,
                            reference_revision_hashes=revised_reference_hashes or None)
    except BaseException:
        dataset_path.write_bytes(before_dataset)
        results_path.write_bytes(before_results)
        raise
    receipt_path = source/'scoring-revisions.json'
    history = json.loads(receipt_path.read_text()) if receipt_path.exists() else []
    receipt = {
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
    }
    history.append(receipt)
    receipt_path.write_text(json.dumps(history, ensure_ascii=False, indent=2)+'\n')
    if prior_reference_receipts is not None:
        receipt_sha = hashlib.sha256(json.dumps(
            receipt, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        current_dataset = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        current_results = hashlib.sha256(results_path.read_bytes()).hexdigest()
        for row in prior_reference_receipts:
            row['prior_output_dataset_sha256'] = row['output_dataset_sha256']
            row['prior_output_results_sha256'] = row['output_results_sha256']
            row['output_dataset_sha256'] = current_dataset
            row['output_results_sha256'] = current_results
            row['rebound_after_scoring_revision_at'] = receipt['scored_at']
            row['rebound_by_scoring_revision_receipt_sha256'] = receipt_sha
        reference_receipt_path.write_text(json.dumps(
            prior_reference_receipts, ensure_ascii=False, indent=2)+'\n')
    rebind_unchanged_result_review(
        review_path, before_dataset, before_results,
        dataset_path.read_bytes(), results_path.read_bytes(),
        rebound_at=receipt['scored_at'])
    return receipt


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--canonical',type=Path,default=ROOT/'evaluation/dataset.json')
    args=parser.parse_args()
    print(json.dumps(revise(args.source,args.canonical),ensure_ascii=False,indent=2))
