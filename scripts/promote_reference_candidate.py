#!/usr/bin/env python3
"""Install a reviewed reference candidate and its independently captured evidence."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from harness.web_fetch.safety import contained_path  # noqa: E402
from scripts.freeze_headless_references import (  # noqa: E402
    _require_promotion, browser_observation_bytes, compute_reference_update,
    load_browser_observations, select_freeze_cases,
)


def promote(*,canonical_path,candidate_path,captures,receipt_path,receipt_target,
            browser_observations=None,browser_observation_directory=None,
            research_root=None,sources_root=None):
    canonical_bytes=canonical_path.read_bytes();canonical=json.loads(canonical_bytes)
    receipt=json.loads(receipt_path.read_text())
    case_ids=[change['case_id'] for change in receipt.get('changes',[])]
    if not case_ids or len(case_ids)!=len(set(case_ids)):
        raise ValueError('Promotion receipt has an invalid affected case inventory')
    selected=select_freeze_cases(deepcopy(canonical),','.join(case_ids))
    observations=load_browser_observations(
        selected,browser_observations,browser_observation_directory)
    expected,changes,contexts=compute_reference_update(
        selected,captures,observations)
    expected_bytes=(json.dumps(expected,ensure_ascii=False,indent=2)+'\n').encode()
    candidate_bytes=candidate_path.read_bytes()
    if candidate_bytes!=expected_bytes:
        raise ValueError('Candidate bytes differ from the shared freeze computation')
    audit_bytes=(captures/'audit.json').read_bytes()
    _require_promotion(receipt_path,
        dataset_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        capture_audit_sha256=hashlib.sha256(audit_bytes).hexdigest(),
        candidate_dataset_sha256=hashlib.sha256(candidate_bytes).hexdigest(),
        changes=changes)
    research_root=(research_root or ROOT/'research'/'reference-freeze-promotions').resolve()
    target=contained_path(research_root,receipt_target.name)
    if receipt_target.resolve()!=target or target.exists():
        raise ValueError('Receipt target must be a new file in research/reference-freeze-promotions')

    updates={case['id']:case for case in expected['cases']}
    final=deepcopy(canonical)
    final['cases']=[updates.get(case['id'],case) for case in final['cases']]
    receipt_bytes=receipt_path.read_bytes();receipt_sha=hashlib.sha256(receipt_bytes).hexdigest()
    final.setdefault('comparison',{})['reference_freeze_promotion']={
        'schema_version':1,
        'receipt':'research/reference-freeze-promotions/'+target.name,
        'receipt_sha256':receipt_sha,
        'approved_by':receipt['approved_by'],'approved_at':receipt['approved_at'],
        'affected_case_ids':case_ids,
    }

    prepared={}
    audit={row['case_id']:row for row in json.loads(audit_bytes)}
    for case_id in case_ids:
        context=contexts[case_id];row=context['row']
        if context['browser_observation']:
            observation=browser_observation_bytes(row)
            source_revision=next(
                change['source_revision'] for change in changes
                if change['case_id']==case_id)
            if (hashlib.sha256(observation).hexdigest()!=
                    source_revision.get('browser_observation_sha256')):
                raise ValueError('Browser observation hash changed: '+case_id)
            if (hashlib.sha256(row['content_text'].encode()).hexdigest()!=
                    source_revision.get('rendered_content_sha256')):
                raise ValueError('Rendered browser content hash changed: '+case_id)
            raw=None;capture_record=None
        else:
            raw=(captures/case_id/'source.html').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ValueError('Captured source hash changed: '+case_id)
            capture_record=dict(audit[case_id]);capture_record['missing_facts']=[]
            capture_record['quality_issues']=[issue for issue in row.get('quality_issues',[])
                if not (issue.startswith('fact_absent_from_reference:')
                        or issue.startswith('duplicate_fact:'))]
            capture_record['state']=('captured' if row.get('status')==200
                                     and not capture_record['quality_issues'] else 'needs_review')
            capture_record=(json.dumps(
                capture_record,ensure_ascii=False,indent=2)+'\n').encode()
        reference=updates[case_id]['ground_truth_main_content'].encode()
        if hashlib.sha256(reference).hexdigest()!=next(
                change['new_reference_sha256'] for change in changes
                if change['case_id']==case_id):
            raise ValueError('Candidate reference hash changed: '+case_id)
        prepared[case_id]=(raw,reference,capture_record,
                           observation if context['browser_observation'] else None)

    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(receipt_path,target)
    sources_root=(sources_root or ROOT/'evaluation'/'sources').resolve()
    for case_id,(raw,reference,capture_record,observation) in prepared.items():
        source=contained_path(sources_root,case_id);source.mkdir(parents=True,exist_ok=True)
        if observation is not None:
            for stale in ('current-source.html.txt','current-capture.json'):
                (source/stale).unlink(missing_ok=True)
            (source/'current-browser-observation.json').write_bytes(observation)
        else:
            (source/'current-browser-observation.json').unlink(missing_ok=True)
            (source/'current-source.html.txt').write_bytes(raw)
            (source/'current-capture.json').write_bytes(capture_record)
        (source/'reference.txt').write_bytes(reference)
    canonical_path.write_text(json.dumps(final,ensure_ascii=False,indent=2)+'\n')
    return {'dataset_sha256':hashlib.sha256(canonical_path.read_bytes()).hexdigest(),
            'receipt_sha256':receipt_sha,'affected_case_ids':case_ids}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canonical',type=Path,default=ROOT/'evaluation/dataset.json')
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--captures',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--receipt-target',type=Path,required=True)
    parser.add_argument('--browser-observations',type=Path)
    parser.add_argument('--browser-observation-directory',type=Path)
    args=parser.parse_args(argv)
    try:
        result=promote(canonical_path=args.canonical,candidate_path=args.candidate,
            captures=args.captures,receipt_path=args.receipt,
            receipt_target=args.receipt_target,
            browser_observations=args.browser_observations,
            browser_observation_directory=args.browser_observation_directory)
    except (OSError,KeyError,ValueError,json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
