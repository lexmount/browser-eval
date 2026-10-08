"""Merge a blinded supplemental review into a prior complete review.

Use after an offline scoring-rule revision changes task verdicts. Unchanged
attempt reviews remain valid because their artifacts and task rules did not
change; every attempt whose complete task or result record changed must be
independently re-reviewed.
"""
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.record_io import load_jsonl, parse_jsonl
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.result_review import (
    require_blind_result_review,
    require_result_review,
    review_input_identity,
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision_suffix(receipts, prior_bindings, current_bindings):
    """Return the complete revision suffix from a prior review to current bytes."""
    starts = [index for index, receipt in enumerate(receipts)
              if (receipt.get('input_dataset_sha256'),
                  receipt.get('input_results_sha256')) == (
                      prior_bindings.get('dataset_sha256'),
                      prior_bindings.get('results_sha256'))]
    if not starts:
        raise ValueError('Prior review is not bound to any scoring revision inputs')

    failures = []
    for start in starts:
        expected = (prior_bindings.get('dataset_sha256'),
                    prior_bindings.get('results_sha256'))
        suffix = receipts[start:]
        for offset, receipt in enumerate(suffix):
            actual_input = (receipt.get('input_dataset_sha256'),
                            receipt.get('input_results_sha256'))
            if actual_input != expected:
                failures.append(
                    f'Scoring revision chain is broken at receipt {start + offset + 1}')
                break
            expected = (receipt.get('output_dataset_sha256'),
                        receipt.get('output_results_sha256'))
            if None in expected:
                failures.append(
                    f'Scoring revision receipt {start + offset + 1} has no output binding')
                break
        else:
            if expected == (current_bindings['dataset_sha256'],
                            current_bindings['results_sha256']):
                return suffix
            failures.append('Scoring revision chain does not end at current evidence')
    raise ValueError(failures[0])


def _validate_before_install(dataset_bytes, results_bytes, review, *, anomaly_gate=True):
    """Run the publication validator before replacing the installed review."""
    with tempfile.TemporaryDirectory(prefix='result-review-merge-') as directory:
        root = Path(directory)
        (root/'dataset.json').write_bytes(dataset_bytes)
        (root/'results.jsonl').write_bytes(results_bytes)
        (root/'result-review.json').write_text(
            json.dumps(review, ensure_ascii=False, indent=2)+'\n')
        dataset = json.loads(dataset_bytes)
        records = parse_jsonl(results_bytes)
        validator = require_result_review if anomaly_gate else require_blind_result_review
        validator(root, dataset, records)


def _install_review(evidence, review, *, anomaly_gate=True):
    dataset_bytes = (evidence/'dataset.json').read_bytes()
    results_bytes = (evidence/'results.jsonl').read_bytes()
    _validate_before_install(
        dataset_bytes, results_bytes, review, anomaly_gate=anomaly_gate)
    target = evidence/'result-review.json'
    payload = json.dumps(review, ensure_ascii=False, indent=2)+'\n'
    temporary = target.with_name('.result-review.json.merge-tmp')
    try:
        temporary.write_text(payload)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    dataset = json.loads(dataset_bytes)
    records = parse_jsonl(results_bytes)
    validator = require_result_review if anomaly_gate else require_blind_result_review
    validator(evidence, dataset, records)
    return target


def merge(evidence: Path, prior_path: Path, supplemental_evidence: Path):
    dataset=json.loads((evidence/'dataset.json').read_text())
    records=load_jsonl(evidence/'results.jsonl')
    receipts=json.loads((evidence/'scoring-revisions.json').read_text())
    prior=json.loads(prior_path.read_text())
    current_bindings={'dataset_sha256':sha(evidence/'dataset.json'),
                      'results_sha256':sha(evidence/'results.jsonl')}
    suffix=_revision_suffix(receipts,prior.get('bindings',{}),current_bindings)
    changed_cases={row['case_id'] for receipt in suffix
                   for row in receipt.get('changed_cases',[])}
    if not changed_cases:
        raise ValueError('Scoring revision chain has no changed cases')
    current_cases={case['id']:case for case in dataset['cases']}
    if not changed_cases <= set(current_cases):
        raise ValueError('Scoring revision changed cases are absent from current dataset')
    subset_dataset=json.loads((supplemental_evidence/'dataset.json').read_text())
    subset_records=load_jsonl(supplemental_evidence/'results.jsonl')
    supplemental=require_blind_result_review(
        supplemental_evidence,subset_dataset,subset_records)
    supplemental_protocol=supplemental.get('review_protocol',{})
    if supplemental_protocol.get('machine_judgments_withheld') is not True:
        raise ValueError('Supplemental review must be blinded to machine judgments')
    if supplemental_protocol.get('scope','all') != 'all':
        raise ValueError('Supplemental review must cover all changed attempts')
    subset_cases={case['id']:case for case in subset_dataset['cases']}
    if set(subset_cases) != changed_cases or any(
            subset_cases[case_id] != current_cases[case_id]
            for case_id in changed_cases):
        raise ValueError('Supplemental review must use every current changed task')
    expected={r['attempt_id'] for r in records if r['case_id'] in changed_cases}
    supplied={r['attempt_id'] for r in subset_records}
    if supplied != expected:
        raise ValueError('Supplemental review must contain every attempt for every changed case')
    current={r['attempt_id']:r for r in records}
    for row in subset_records:
        if row != current[row['attempt_id']]:
            raise ValueError('Supplemental review was not run against current persisted results')
    replacements={row['attempt_id']:row for row in supplemental['attempts']}
    if set(replacements) != expected:
        raise ValueError('Supplemental review must independently review every changed attempt')
    previous_attempts=prior.get('attempts',[])
    previous_ids=[row.get('attempt_id') for row in previous_attempts]
    if len(previous_ids) != len(set(previous_ids)) or set(previous_ids) != set(current):
        raise ValueError('Prior full review does not cover the current attempt inventory')
    retained={row['attempt_id']:row for row in previous_attempts
              if current[row['attempt_id']]['case_id'] not in changed_cases}
    attempts=[replacements.get(row['attempt_id'],retained.get(row['attempt_id']))
              for row in records]
    if any(row is None for row in attempts):
        raise ValueError('Merged review does not cover the current attempt inventory')
    merged=json.loads(json.dumps(prior))
    merged['bindings']=current_bindings
    merged['attempts']=attempts
    merged.setdefault('review_protocol',{})['scope']='all'
    merged['review_protocol']['scoring_rule_revision_review']={
        'changed_cases':sorted(changed_cases),
        'reviewed_attempts':len(replacements),
        'reviewer':supplemental['reviewer'],
        'machine_judgments_withheld':True,
        'revision_receipts':len(suffix),
    }
    merged['total_attempts']=len(records)
    merged['expected_attempts']=len(records)
    merged['reviewed_attempts']=len(attempts)
    merged['accepted_unreviewed_passes']=0
    merged['disagreements']=sum(row['agent_verdict'] != row['machine_verdict'] for row in attempts)
    merged['unresolved']=sum(row['final_verdict'] not in {'pass','fail'} for row in attempts)
    anomaly_gate = not dataset.get('comparison', {}).get('require_anomaly_review', False)
    return _install_review(evidence,merged,anomaly_gate=anomaly_gate)


def _unique_by(rows, key, label):
    if not rows:
        raise ValueError(f'{label} must not be empty')
    values = [row.get(key) for row in rows]
    if None in values or len(values) != len(set(values)):
        raise ValueError(f'{label} must have unique non-empty {key} values')
    return dict(zip(values, rows))


def merge_refresh(evidence: Path, prior_evidence: Path,
                  supplemental_evidence: Path, prior_review_path: Path | None = None):
    """Safely reuse reviews across an arbitrarily refreshed complete matrix.

    A prior per-attempt review is reusable only when the complete current case
    and every reviewer-visible evidence binding are unchanged, and the prior
    independent verdict still agrees with the current machine verdict. This
    ignores timestamps and diagnostic metrics that were never shown to the
    reviewer, while invalidating all providers for a changed task.
    """
    current_dataset=json.loads((evidence/'dataset.json').read_text())
    current_records=load_jsonl(evidence/'results.jsonl')
    prior_dataset=json.loads((prior_evidence/'dataset.json').read_text())
    prior_records=load_jsonl(prior_evidence/'results.jsonl')
    if prior_review_path is None:
        # The current anomaly detector may intentionally be stricter than the
        # one available when the prior blind review was recorded. Reuse only
        # its attempt-complete blind verdicts here; the assembled current
        # matrix must pass a fresh anomaly audit before publication.
        prior=require_blind_result_review(prior_evidence,prior_dataset,prior_records)
    else:
        prior=json.loads(prior_review_path.read_text())
        if prior.get('bindings') != {
                'dataset_sha256':sha(prior_evidence/'dataset.json'),
                'results_sha256':sha(prior_evidence/'results.jsonl')}:
            raise ValueError('Staged prior review is bound to different evidence')
        protocol=prior.get('review_protocol', {})
        if (protocol.get('machine_judgments_withheld') is not True
                or protocol.get('production_scorer_forbidden') is not True
                or protocol.get('direct_artifact_evidence_required') is not True):
            raise ValueError('Staged prior review is not an independent blind review')
        if prior.get('unresolved') != 0:
            raise ValueError('Staged prior review has unresolved judgments')

    current_cases=_unique_by(current_dataset.get('cases', []), 'id', 'Current dataset')
    prior_cases=_unique_by(prior_dataset.get('cases', []), 'id', 'Prior dataset')
    current=_unique_by(current_records, 'attempt_id', 'Current results')
    previous=_unique_by(prior_records, 'attempt_id', 'Prior results')
    prior_attempts=_unique_by(prior.get('attempts', []), 'attempt_id', 'Prior review')
    if any(row.get('case_id') not in current_cases for row in current_records):
        raise ValueError('Current result refers to a task absent from the current dataset')

    prior_protocol=prior.get('review_protocol', {})
    if prior_protocol.get('scope', 'all') != 'all':
        raise ValueError('Prior review must be a complete full-scope review')
    if prior.get('accepted_unreviewed_passes', 0) != 0:
        raise ValueError('Prior review must not contain accepted unreviewed passes')

    reusable=set()
    for attempt_id,row in current.items():
        old=previous.get(attempt_id)
        case_id=row['case_id']
        current_machine = ('pass' if task_passed(
            row['ok'], row['metrics'], current_cases[case_id].get('task_kind', 'body'))
            else 'fail')
        if (old is not None and old.get('case_id') == case_id
                and prior_cases.get(case_id) == current_cases[case_id]
                and review_input_identity(old) == review_input_identity(row)
                and prior_attempts.get(attempt_id, {}).get('final_verdict') == current_machine):
            reusable.add(attempt_id)
    required=set(current)-reusable
    if not required:
        raise ValueError('Refresh has no attempts requiring supplemental review')

    subset_dataset=json.loads((supplemental_evidence/'dataset.json').read_text())
    subset_records=load_jsonl(supplemental_evidence/'results.jsonl')
    supplemental=require_blind_result_review(
        supplemental_evidence,subset_dataset,subset_records)
    if supplemental is None:
        raise ValueError('Supplemental independent result review is required')
    supplemental_protocol=supplemental.get('review_protocol', {})
    if supplemental_protocol.get('machine_judgments_withheld') is not True:
        raise ValueError('Supplemental review must be blinded to machine judgments')
    if supplemental_protocol.get('scope', 'all') != 'all':
        raise ValueError('Supplemental review must cover every non-reusable attempt')
    if supplemental.get('accepted_unreviewed_passes', 0) != 0:
        raise ValueError('Supplemental review must not contain accepted unreviewed passes')

    subset_cases=_unique_by(subset_dataset.get('cases', []), 'id', 'Supplemental dataset')
    required_cases={current[attempt_id]['case_id'] for attempt_id in required}
    if set(subset_cases)!=required_cases or any(
            subset_cases[case_id]!=current_cases[case_id]
            for case_id in required_cases):
        raise ValueError('Supplemental review must use every exact current required task')
    supplied_records=_unique_by(subset_records, 'attempt_id', 'Supplemental results')
    if set(supplied_records)!=required:
        raise ValueError('Supplemental review must contain exactly every non-reusable current attempt')
    if any(row!=current[attempt_id]
           for attempt_id,row in supplied_records.items()):
        raise ValueError('Supplemental review was not run against exact current result records')

    replacements={row['attempt_id']:row for row in supplemental['attempts']}
    if set(replacements) != required:
        raise ValueError('Supplemental review must independently review every non-reusable attempt')
    retained={attempt_id:prior_attempts[attempt_id] for attempt_id in reusable}
    attempts=[replacements.get(row['attempt_id'],retained.get(row['attempt_id']))
              for row in current_records]
    if any(row is None for row in attempts):
        raise ValueError('Merged review is missing an attempt')
    # Retained blind judgments may come from a staged review that already
    # identified a scorer error. After the scorer is corrected, carry the
    # independent judgment forward but bind machine/final verdict fields to
    # the current persisted score. Reviewer-visible inputs were checked above.
    normalized_attempts=[]
    for record,reviewed in zip(current_records,attempts):
        machine=('pass' if task_passed(
            record['ok'],record['metrics'],
            current_cases[record['case_id']].get('task_kind','body')) else 'fail')
        reviewed=dict(reviewed)
        reviewed['machine_verdict']=machine
        reviewed['final_verdict']=machine
        if reviewed.get('agent_verdict') == machine:
            reviewed['adjudication']=None
        elif not str(reviewed.get('adjudication') or '').strip():
            raise ValueError('Retained disagreement lacks adjudication: '+record['attempt_id'])
        normalized_attempts.append(reviewed)
    attempts=normalized_attempts
    merged=json.loads(json.dumps(prior))
    merged['bindings']={'dataset_sha256':sha(evidence/'dataset.json'),
                        'results_sha256':sha(evidence/'results.jsonl')}
    merged['attempts']=attempts
    changed_task_cases={case_id for case_id in current_cases
                        if prior_cases.get(case_id) != current_cases[case_id]}
    rerun_only_attempts=required-{attempt_id for attempt_id in required
                                  if current[attempt_id]['case_id'] in changed_task_cases}
    merged.setdefault('review_protocol',{})['matrix_refresh_review']={
        'changed_task_cases':sorted(changed_task_cases),
        'rerun_only_attempts':sorted(rerun_only_attempts),
        'retained_attempts':len(retained),
        'reviewed_attempts':len(replacements),
        'reviewer':supplemental['reviewer'],
        'machine_judgments_withheld':True,
        'reuse_rule':'Only identical complete task definitions and identical reviewer-visible evidence bindings retained prior reviews; the prior adjudicated final verdict must still equal the current machine verdict.',
    }
    merged['total_attempts']=len(current_records)
    merged['expected_attempts']=len(current_records)
    merged['reviewed_attempts']=len(attempts)
    merged['accepted_unreviewed_passes']=0
    merged['disagreements']=sum(row['agent_verdict']!=row['machine_verdict'] for row in attempts)
    merged['unresolved']=sum(row['final_verdict'] not in {'pass','fail'} for row in attempts)
    merged['confirmed_scoring_errors']=sum(
        row['final_verdict']!=row['machine_verdict'] for row in attempts)
    anomaly_gate = not current_dataset.get('comparison', {}).get(
        'require_anomaly_review', False)
    return _install_review(evidence,merged,anomaly_gate=anomaly_gate)


def merge_replacement(evidence: Path, prior_evidence: Path,
                      supplemental_evidence: Path):
    """Backward-compatible name for the generalized matrix refresh merge."""
    return merge_refresh(evidence, prior_evidence, supplemental_evidence)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--prior-review',type=Path)
    parser.add_argument('--supplemental-evidence',type=Path,required=True)
    parser.add_argument('--prior-evidence',type=Path)
    args=parser.parse_args()
    if args.prior_evidence:
        print(merge_refresh(
            args.evidence,args.prior_evidence,args.supplemental_evidence,
            args.prior_review))
    else:
        if not args.prior_review:
            parser.error('--prior-review is required without --prior-evidence')
        print(merge(args.evidence,args.prior_review,args.supplemental_evidence))
