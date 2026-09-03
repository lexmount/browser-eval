"""Audit complete measured evidence, including a publication-blocked cohort."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.config import load_dataset
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.runner import load_list_reference


def check(root,rescore=False):
    dataset,typed=load_dataset(root/'dataset.json')
    cases={c['id']:c for c in dataset['cases']};typed={c.id:c for c in typed}
    original={c['id']:c for c in json.loads((ROOT/'evaluation/dataset.json').read_text())['cases']}
    assert cases.keys()==original.keys(),'Task inventory differs'
    for cid in cases:
        a,b=deepcopy(cases[cid]),deepcopy(original[cid])
        for c in (a,b):c.pop('source_verification',None);c.pop('ground_truth_main_content',None)
        assert a==b,('Task rule changed',cid)
    records=[json.loads(line) for line in (root/'results.jsonl').read_text().splitlines()]
    names=set(json.loads((ROOT/'configs/providers.headless.json').read_text())['providers'])
    validate_report_ids(dataset,records)
    assert len(records)==len(cases)*len(names)
    assert {(r['provider'],r['case_id']) for r in records}=={(n,cid) for n in names for cid in cases}
    assert len({r['attempt_id'] for r in records})==len(records)
    for r in records:
        raw=contained_path(root,r['artifact_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==r['metrics']['content_sha256'],r['attempt_id']
        meta=r['provider_metadata'];assert meta['engine']==r['provider']
        for path_key,hash_key in [('rendered_html_path','rendered_html_sha256'),('raw_stdout_path','raw_stdout_sha256')]:
            if meta.get(path_key):assert hashlib.sha256(contained_path(root,meta[path_key]).read_bytes()).hexdigest()==meta[hash_key]
        if rescore:
            case=typed[r['case_id']]
            measured=score_attempt(case,raw,ok=r['ok'],metadata=meta,list_reference=load_list_reference(case,root))
            for key,value in measured.items():
                assert r['metrics'].get(key)==value,(r['attempt_id'],key)
    batches=json.loads((root/'execution.json').read_text())['batches']
    assert Counter(i for b in batches for i in b['attempt_ids'])==Counter(r['attempt_id'] for r in records)
    valid=[b for b in batches if b.get('reference_checks',{}).get('status','verified')=='verified']
    require_verified_reference_checks(valid,cases=dataset,records=records,source=root)
    failed=sorted({cid for b in batches for cid,row in b.get('reference_checks',{}).get('postcheck',{}).get('cases',{}).items() if row['status']!='verified'})
    assert failed==dataset['comparison']['unverified_reference_cases']
    print('PASS:',len(cases),'tasks,',len(records),'unique calls; hashes, rules, identities and available certificates verified')
    if rescore:print('PASS: all persisted metrics reproduce without changing outputs or scores')
    print('Publication reference blockers:',failed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,nargs='?',default=ROOT/'evaluation/headless')
    parser.add_argument('--rescore',action='store_true')
    args=parser.parse_args();check(args.source,args.rescore)
