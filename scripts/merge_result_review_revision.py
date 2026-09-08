"""Merge a blinded review of changed cases into the prior complete review.

Use after an offline scoring-rule revision changes task verdicts. Unchanged
attempt reviews remain valid because their artifacts and task rules did not
change; every attempt for each changed case must be independently re-reviewed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.result_review import require_result_review


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def merge(evidence: Path, prior_path: Path, supplemental_evidence: Path):
    dataset=json.loads((evidence/'dataset.json').read_text())
    records=[json.loads(line) for line in (evidence/'results.jsonl').read_text().splitlines()]
    receipts=json.loads((evidence/'scoring-revisions.json').read_text())
    receipt=receipts[-1]
    changed_cases={row['case_id'] for row in receipt.get('changed_cases',[])}
    if not changed_cases:
        raise ValueError('Latest scoring revision has no changed cases')
    prior=json.loads(prior_path.read_text())
    if prior.get('bindings') != {
            'dataset_sha256':receipt['input_dataset_sha256'],
            'results_sha256':receipt['input_results_sha256']}:
        raise ValueError('Prior review is not bound to the scoring revision inputs')
    subset_dataset=json.loads((supplemental_evidence/'dataset.json').read_text())
    subset_records=[json.loads(line) for line in (supplemental_evidence/'results.jsonl').read_text().splitlines()]
    supplemental=require_result_review(supplemental_evidence,subset_dataset,subset_records)
    expected={r['attempt_id'] for r in records if r['case_id'] in changed_cases}
    supplied={r['attempt_id'] for r in subset_records}
    if supplied != expected:
        raise ValueError('Supplemental review must contain every attempt for every changed case')
    current={r['attempt_id']:r for r in records}
    for row in subset_records:
        if row != current[row['attempt_id']]:
            raise ValueError('Supplemental review was not run against current persisted results')
    replacements={row['attempt_id']:row for row in supplemental['attempts']}
    attempts=[replacements.get(row['attempt_id'],row) for row in prior['attempts']]
    if {row['attempt_id'] for row in attempts} != set(current):
        raise ValueError('Merged review does not cover the current attempt inventory')
    prior['bindings']={'dataset_sha256':sha(evidence/'dataset.json'),
                       'results_sha256':sha(evidence/'results.jsonl')}
    prior['attempts']=attempts
    prior['review_protocol']['scoring_rule_revision_review']={
        'changed_cases':sorted(changed_cases),
        'reviewed_attempts':len(replacements),
        'reviewer':supplemental['reviewer'],
        'machine_judgments_withheld':True,
    }
    prior['disagreements']=sum(row['agent_verdict'] != row['machine_verdict'] for row in attempts)
    prior['unresolved']=sum(row['final_verdict'] not in {'pass','fail'} for row in attempts)
    target=evidence/'result-review.json'
    target.write_text(json.dumps(prior,ensure_ascii=False,indent=2)+'\n')
    require_result_review(evidence,dataset,records)
    return target


def merge_replacement(evidence: Path, prior_evidence: Path,
                      supplemental_evidence: Path):
    """Reuse reviews only for byte-identical tasks/results after case replacement.

    A changed task invalidates every old review for that case, but it does not
    invalidate reviews for other cases whose task definitions and complete
    result records are unchanged. The supplemental bundle must contain every
    current attempt for every changed case and its own blinded review.
    """
    current_dataset=json.loads((evidence/'dataset.json').read_text())
    current_records=[json.loads(line) for line in (evidence/'results.jsonl').read_text().splitlines()]
    prior_dataset=json.loads((prior_evidence/'dataset.json').read_text())
    prior_records=[json.loads(line) for line in (prior_evidence/'results.jsonl').read_text().splitlines()]
    prior=require_result_review(prior_evidence,prior_dataset,prior_records)

    current_cases={case['id']:case for case in current_dataset['cases']}
    prior_cases={case['id']:case for case in prior_dataset['cases']}
    if set(current_cases)!=set(prior_cases):
        raise ValueError('Task replacement must preserve the case inventory')
    changed_cases={case_id for case_id in current_cases
                   if current_cases[case_id]!=prior_cases[case_id]}
    if not changed_cases:
        raise ValueError('Task replacement has no changed cases')

    current={row['attempt_id']:row for row in current_records}
    previous={row['attempt_id']:row for row in prior_records}
    if set(current)!=set(previous):
        raise ValueError('Task replacement must preserve the attempt inventory')
    for attempt_id,row in current.items():
        if row['case_id'] not in changed_cases and row!=previous[attempt_id]:
            raise ValueError('Unchanged case result differs: '+attempt_id)

    subset_dataset=json.loads((supplemental_evidence/'dataset.json').read_text())
    subset_records=[json.loads(line) for line in (supplemental_evidence/'results.jsonl').read_text().splitlines()]
    supplemental=require_result_review(supplemental_evidence,subset_dataset,subset_records)
    subset_cases={case['id']:case for case in subset_dataset['cases']}
    if set(subset_cases)!=changed_cases or any(
            subset_cases[case_id]!=current_cases[case_id] for case_id in changed_cases):
        raise ValueError('Supplemental review must use every current changed task')
    expected={attempt_id for attempt_id,row in current.items()
              if row['case_id'] in changed_cases}
    supplied={row['attempt_id'] for row in subset_records}
    if supplied!=expected or any(row!=current[row['attempt_id']]
                                 for row in subset_records):
        raise ValueError('Supplemental review must contain every current changed attempt')

    replacements={row['attempt_id']:row for row in supplemental['attempts']}
    retained={row['attempt_id']:row for row in prior['attempts']
              if previous[row['attempt_id']]['case_id'] not in changed_cases}
    attempts=[replacements.get(row['attempt_id'],retained.get(row['attempt_id']))
              for row in current_records]
    if any(row is None for row in attempts):
        raise ValueError('Merged review is missing an attempt')
    merged=json.loads(json.dumps(prior))
    merged['bindings']={'dataset_sha256':sha(evidence/'dataset.json'),
                        'results_sha256':sha(evidence/'results.jsonl')}
    merged['attempts']=attempts
    merged.setdefault('review_protocol',{})['task_replacement_review']={
        'changed_cases':sorted(changed_cases),
        'reviewed_attempts':len(replacements),
        'reviewer':supplemental['reviewer'],
        'machine_judgments_withheld':True,
        'reuse_rule':'Only byte-identical task definitions and result records retained prior reviews.',
    }
    merged['total_attempts']=len(current_records)
    merged['expected_attempts']=len(current_records)
    merged['reviewed_attempts']=len(attempts)
    merged['accepted_unreviewed_passes']=0
    merged['disagreements']=sum(row['agent_verdict']!=row['machine_verdict'] for row in attempts)
    merged['unresolved']=sum(row['final_verdict'] not in {'pass','fail'} for row in attempts)
    target=evidence/'result-review.json'
    target.write_text(json.dumps(merged,ensure_ascii=False,indent=2)+'\n')
    require_result_review(evidence,current_dataset,current_records)
    return target


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--prior-review',type=Path)
    parser.add_argument('--supplemental-evidence',type=Path,required=True)
    parser.add_argument('--prior-evidence',type=Path)
    args=parser.parse_args()
    if args.prior_evidence:
        print(merge_replacement(args.evidence,args.prior_evidence,args.supplemental_evidence))
    else:
        if not args.prior_review:
            parser.error('--prior-review is required without --prior-evidence')
        print(merge(args.evidence,args.prior_review,args.supplemental_evidence))
