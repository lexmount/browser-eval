"""Certify completed matched cases promptly, retaining original live bindings.

This partitions a live, preflighted run; it cannot certify a legacy run. Each
projection retains exact attempt IDs/records and the source manifest bytes.
No failed postcheck is overwritten and no observation timestamp is invented.
"""
import argparse
import hashlib
import json
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.record_io import read_completed_records
from harness.web_fetch.reference_lifecycle import finalize_reference_checks
from harness.web_fetch.safety import safe_identifier


def certify_case(run, output, case, records, observation):
    source_raw=(run/'manifest.json').read_bytes()
    source=json.loads(source_raw)
    cid=safe_identifier(case['id'])
    if source.get('reference_checks',{}).get('status')!='awaiting_postcheck' or 'postcheck' in source.get('reference_checks',{}):
        raise ValueError('Only an unfinished live preflight can be partitioned')
    binding=source.get('reference_checks',{}).get('cases',{}).get(cid)
    if not binding:
        raise ValueError('No original pre-call reference binding: '+cid)
    if any(r['case_id']!=cid for r in records):
        raise ValueError('Mixed case records')
    dataset=json.loads((run/'dataset.json').read_text())
    expected=set(dataset['coverage_plan'][case.get('track','web')])
    if len(records)!=len(expected) or {r['provider'] for r in records}!=expected:
        raise ValueError('Incomplete matched-provider cohort')
    destination=output/cid
    destination.mkdir(parents=True,exist_ok=False)
    manifest=deepcopy(source)
    # End of this assembly operation, not a newly invented call timestamp.
    # perf_counter starts before the wall-clock call timestamp in the runner;
    # start + elapsed can therefore slightly exceed recorded completed_at.
    manifest.update(attempt_count=len(records),attempt_ids=[r['attempt_id'] for r in records],
        completed_at=datetime.now(timezone.utc).isoformat())
    manifest['reference_checks']={k:deepcopy(v) for k,v in source['reference_checks'].items()
        if k not in ('cases','postcheck','callcheck_error')}
    # Preserve the original preflight status; never clear a failed lifecycle.
    manifest['reference_checks']['cases']={cid:deepcopy(binding)}
    manifest['projection']={'kind':'completed-case-from-live-preflight',
        'source_manifest_sha256':hashlib.sha256(source_raw).hexdigest(),
        'source_manifest_path':'source-manifest.json',
        'created_at':datetime.now(timezone.utc).isoformat(),
        'original_attempt_ids_preserved':True}
    dataset['cases']=[case]
    dataset['coverage_plan']={case.get('track','web'):sorted(expected)}
    manifest.setdefault('dataset',{})['case_count']=1
    (destination/'source-manifest.json').write_bytes(source_raw)
    for name,payload in [('manifest.json',manifest),('dataset.json',dataset),('observations.json',[observation])]:
        (destination/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    (destination/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    result=finalize_reference_checks(destination,destination/'observations.json')
    print(cid,result['status'],flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--observations',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=2700)
    args=parser.parse_args()
    dataset=json.loads((args.run_dir/'dataset.json').read_text())
    cases={c['id']:c for c in dataset['cases'] if c.get('track')=='web'}
    deadline=time.monotonic()+args.timeout
    while time.monotonic()<deadline:
        try:
            observations=json.loads(args.observations.read_text())
        except (FileNotFoundError,json.JSONDecodeError):
            time.sleep(1);continue
        records=read_completed_records(args.run_dir/'results.jsonl')
        for observation in observations:
            cid=observation['case_id']
            if (args.output/cid).exists():continue
            certify_case(args.run_dir,args.output,cases[cid],
                [r for r in records if r['case_id']==cid],observation)
        if all((args.output/cid/'manifest.json').exists() for cid in cases):return
        time.sleep(1)
    raise TimeoutError('Case certification deadline')


if __name__=='__main__':main()
