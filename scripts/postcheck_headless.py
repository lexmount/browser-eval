"""Acquire independent post-call observations as each matched case completes.

This is part of the measurement protocol, not provider output-based reference
construction. Only case IDs and completion times are read from result records.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.collect_source_evidence import capture
from harness.web_fetch.reference_lifecycle import requires_fresh_reference
from harness.web_fetch.record_io import read_completed_records


def frozen_scope_identities(run_dir, cases):
    """Return the exact scope identities frozen by the measured run."""
    execution=json.loads((run_dir/'execution.json').read_text())
    identities={}
    for batch in execution.get('batches',[]):
        checks=batch.get('reference_checks',{})
        for case_id,binding in checks.get('cases',{}).items():
            if case_id not in cases:
                continue
            identity=binding.get('scope_identity')
            if not isinstance(identity,dict):
                raise ValueError(f'Missing frozen scope identity: {case_id}')
            previous=identities.setdefault(case_id,identity)
            if previous!=identity:
                raise ValueError(f'Conflicting frozen scope identities: {case_id}')
    missing=set(cases)-set(identities)
    if missing:
        raise ValueError(f'Missing frozen reference bindings: {sorted(missing)}')
    return identities

def load_resumable_observations(checkpoint, cases):
    if not checkpoint.exists():
        return {}
    return {row['case_id']:row for row in json.loads(checkpoint.read_text())
            if row.get('case_id') in cases}


def expected_providers(dataset, case):
    """Return the declared provider cohort for this case's task group."""
    return set(dataset['coverage_plan'][case.get('track','web')])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--captures',type=Path,required=True)
    p.add_argument('--browser-observations',type=Path,
                   help='Post-call browser observations for browser-contract or HTTP-unavailable references')
    p.add_argument('--timeout',type=int,default=3600)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    dataset=json.loads((args.run_dir/'dataset.json').read_text())
    cases={c['id']:c for c in dataset['cases'] if requires_fresh_reference(c)}
    identities=frozen_scope_identities(args.run_dir,cases)
    if args.captures.exists() and not args.resume:p.error('Capture destination exists')
    args.captures.mkdir(parents=True,exist_ok=args.resume)
    pending={};done={};started=time.monotonic()
    checkpoint=args.captures/'observations.json'
    if args.resume and checkpoint.exists():
        done=load_resumable_observations(checkpoint,cases)
    special={'javascript-spa','live-025-api-reference'}
    def observe(case):
        begin=time.monotonic()
        existing=args.captures/case['id']/'evidence.json'
        row=json.loads(existing.read_text()) if args.resume and existing.exists() else capture(case,args.captures)
        text_path=args.captures/case['id']/'reference.txt'
        return {'case_id':case['id'],'independent':True,
            'checked_at':row['checked_at'],
            'content_text':text_path.read_text() if text_path.exists() else '',
            'scope_identity':identities[case['id']],
            'completion':{'satisfied':row.get('status')==200 and not row.get('missing_facts'),
                          'elapsed_ms':(time.monotonic()-begin)*1000,'timeout_ms':120000}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        while len(done)<len(cases):
            if time.monotonic()-started>args.timeout:raise TimeoutError('Post-call observation deadline')
            records=read_completed_records(args.run_dir/'results.jsonl')
            for cid,case in cases.items():
                relevant=[r for r in records if r['case_id']==cid]
                expected=expected_providers(dataset,case)
                if {r['provider'] for r in relevant}!=expected or cid in done or cid in pending:
                    continue
                rows=(json.loads(args.browser_observations.read_text())
                      if args.browser_observations and args.browser_observations.exists() else {})
                if cid in special or cid in rows:
                    if cid not in rows:continue
                    row=rows.get(cid,{})
                    latest=max(r.get('completed_at',r['started_at']) for r in relevant)
                    if row.get('checked_at','')>latest:
                        done[cid]={'case_id':cid,**row,'scope_identity':identities[cid]}
                    continue
                pending[cid]=pool.submit(observe,case)
            for cid,future in list(pending.items()):
                if future.done():
                    done[cid]=future.result();del pending[cid]
            (args.captures/'observations.json').write_text(json.dumps(list(done.values()),ensure_ascii=False,indent=2)+'\n')
            if len(done)<len(cases):time.sleep(1)
    print('Post-call observations complete:',len(done),flush=True)


if __name__=='__main__':main()
