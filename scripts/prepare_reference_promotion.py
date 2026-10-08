#!/usr/bin/env python3
"""Prepare an explicitly authorized reference-freeze promotion receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.scoring_regressions import (  # noqa: E402
    ROOT_CAUSE_CATEGORIES,
    build_correction_provenance,
)
from harness.web_fetch.reference_freeze import (  # noqa: E402
    REFERENCE_PROMOTION_REGRESSION_IDS,
    _validate_approval,
    compute_reference_update,
    load_browser_observations,
    select_freeze_cases,
)


def prepare(*,dataset_path,captures,output,approved_by,approved_at,
            case_ids=None,exclude_case_ids=None,browser_observations=None,
            browser_observation_directory=None,candidate_output=None,
            full_canonical_output=None,root_cause_category=None,
            root_cause=None,regression_ids=()):
    if output.exists():
        raise ValueError('Destination exists; promotion receipts are immutable')
    for path in (candidate_output,full_canonical_output):
        if path is not None and path.exists():
            raise ValueError('Destination exists; candidate datasets are immutable')
    dataset_bytes=read_dataset_bytes(dataset_path)
    dataset=select_freeze_cases(json.loads(dataset_bytes),case_ids,exclude_case_ids)
    observations=load_browser_observations(
        dataset,browser_observations,browser_observation_directory)
    candidate,changes,_=compute_reference_update(dataset,captures,observations)
    if not changes:
        raise ValueError('Captured reference scope is unchanged; no promotion is needed')
    audit_bytes=(captures/'audit.json').read_bytes()
    candidate_bytes=(json.dumps(candidate,ensure_ascii=False,indent=2)+'\n').encode()
    provenance=build_correction_provenance(
        root_cause_category,root_cause,list(regression_ids))
    if not REFERENCE_PROMOTION_REGRESSION_IDS.issubset(
            set(provenance['regression_ids'])):
        raise ValueError('Reference promotion requires paired scope regressions')
    receipt={'schema_version':2,'kind':'reference_freeze_promotion',
        'input_dataset_sha256':hashlib.sha256(dataset_bytes).hexdigest(),
        'capture_audit_sha256':hashlib.sha256(audit_bytes).hexdigest(),
        'candidate_dataset_sha256':hashlib.sha256(candidate_bytes).hexdigest(),
        'changes':changes,'approved_by':approved_by,'approved_at':approved_at,
        'task_decision_changes':[],
        'correction_provenance':provenance}
    _validate_approval(receipt)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    if candidate_output is not None:
        candidate_output.parent.mkdir(parents=True,exist_ok=True)
        candidate_output.write_bytes(candidate_bytes)
    if full_canonical_output is not None:
        original=json.loads(dataset_bytes)
        updated={case['id']:case for case in candidate['cases']}
        original['cases']=[updated.get(case['id'],case) for case in original['cases']]
        full_canonical_output.parent.mkdir(parents=True,exist_ok=True)
        full_canonical_output.write_text(
            json.dumps(original,ensure_ascii=False,indent=2)+'\n')
    return receipt,candidate_bytes


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'evaluation/dataset.json')
    parser.add_argument('--captures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--approved-by',required=True)
    parser.add_argument('--approved-at',required=True)
    parser.add_argument('--case-ids')
    parser.add_argument('--exclude-case-ids')
    parser.add_argument('--browser-observations',type=Path)
    parser.add_argument('--browser-observation-directory',type=Path)
    parser.add_argument('--candidate-output',type=Path,
                        help='Optional exact selected-case dataset expected by freeze')
    parser.add_argument('--full-canonical-output',type=Path,
                        help='Optional full dataset candidate with only selected cases promoted')
    parser.add_argument('--root-cause-category',required=True,
                        choices=sorted(ROOT_CAUSE_CATEGORIES))
    parser.add_argument('--root-cause',required=True,
                        help='Concrete defect mechanism, not the observed score change')
    parser.add_argument('--regression-id',action='append',required=True,
                        help='Executable case id from evaluation/scoring-regressions.json; repeat as needed')
    args=parser.parse_args(argv)
    try:
        receipt,_=prepare(dataset_path=args.dataset,captures=args.captures,
            output=args.output,approved_by=args.approved_by,approved_at=args.approved_at,
            case_ids=args.case_ids,exclude_case_ids=args.exclude_case_ids,
            browser_observations=args.browser_observations,
            browser_observation_directory=args.browser_observation_directory,
            candidate_output=args.candidate_output,
            full_canonical_output=args.full_canonical_output,
            root_cause_category=args.root_cause_category,
            root_cause=args.root_cause,regression_ids=args.regression_id)
    except (OSError,KeyError,ValueError,json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps({'output':str(args.output),'changes':receipt['changes']},
                     ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
