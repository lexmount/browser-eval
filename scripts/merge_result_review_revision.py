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


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--prior-review',type=Path,required=True)
    parser.add_argument('--supplemental-evidence',type=Path,required=True)
    args=parser.parse_args()
    print(merge(args.evidence,args.prior_review,args.supplemental_evidence))
