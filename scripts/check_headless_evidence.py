"""Audit complete measured evidence, including a publication-blocked cohort."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.config import load_dataset
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.runner import load_list_reference
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.storage import require_current_report_profile


def require(condition,message):
    if not condition:
        raise ValueError(message)


def check(root,rescore=False):
    dataset,typed=load_dataset(root/'dataset.json')
    cases={c['id']:c for c in dataset['cases']};typed={c.id:c for c in typed}
    original={c['id']:c for c in json.loads(read_dataset_bytes(ROOT/'evaluation/dataset.json'))['cases']}
    require(cases.keys()==original.keys(),'Task inventory differs')
    for cid in cases:
        a,b=deepcopy(cases[cid]),deepcopy(original[cid])
        # Body regions are deterministic projections of the independently
        # captured reference text. They may change with a verified reference
        # snapshot and are checked against that snapshot immediately below;
        # they are not a canonical task-rule difference.
        for c in (a,b):
            c.pop('source_verification',None)
            c.pop('ground_truth_main_content',None)
            c.pop('body_requirements',None)
        require(a==b,('Task rule changed',cid))
        if cases[cid].get('body_requirements') is not None:
            # The canonical dataset may contain a reviewed case-specific
            # threshold when ordering is not invariant across renderers.  The
            # evidence must match that declared rule exactly; recomputing the
            # generic default here would silently erase the reviewed exception.
            require(cases[cid]['body_requirements']==original[cid].get('body_requirements'),
                    ('Body requirements differ from canonical task rule',cid))
    require_current_report_profile(root, ROOT/'evaluation/current-report.json')
    records=[json.loads(line) for line in (root/'results.jsonl').read_text().splitlines()]
    provider_configs=json.loads((ROOT/'configs/providers.headless.json').read_text())['providers']
    names=set(provider_configs)
    provenance={row['binary_sha256']:row for row in json.loads(
        (ROOT/'research/headless-implementation-provenance.json').read_text()).get('implementations',[])}
    validate_report_ids(dataset,records)
    require(len(records)==len(cases)*len(names),'Incomplete call count')
    require({(r['provider'],r['case_id']) for r in records}=={(n,cid) for n in names for cid in cases},'Provider/task coverage differs')
    require(len({r['attempt_id'] for r in records})==len(records),'Duplicate attempt IDs')
    for r in records:
        raw=contained_path(root,r['artifact_path']).read_bytes()
        require(hashlib.sha256(raw).hexdigest()==r['metrics']['content_sha256'],('Output hash mismatch',r['attempt_id']))
        meta=r['provider_metadata']
        if meta.get('engine') is not None:
            require(meta['engine']==provider_configs[r['provider']]['engine'],('Engine mismatch',r['attempt_id']))
        if meta.get('deployment') == 'local process':
            require(meta.get('binary_sha256'), ('Local implementation fingerprint missing',r['attempt_id']))
        if meta.get('binary_sha256'):
            identity=provenance.get(meta['binary_sha256'])
            require(meta.get('browser_version') or identity,
                    ('Unidentified local implementation',r['attempt_id'],meta['binary_sha256']))
            if identity:
                require(identity['provider']==r['provider'],
                        ('Implementation fingerprint belongs to another provider',r['attempt_id']))
        for path_key,hash_key in [('rendered_html_path','rendered_html_sha256'),('raw_stdout_path','raw_stdout_sha256')]:
            if meta.get(path_key):require(hashlib.sha256(contained_path(root,meta[path_key]).read_bytes()).hexdigest()==meta[hash_key],('Supplemental hash mismatch',r['attempt_id'],path_key))
        if rescore:
            case=typed[r['case_id']]
            measured=score_attempt(case,raw,ok=r['ok'],status_code=r.get('status_code'),metadata=meta,
                previous_metrics=r['metrics'],list_reference=load_list_reference(case,root))
            for key,value in measured.items():
                require(r['metrics'].get(key)==value,('Metric mismatch',r['attempt_id'],key))
    # Quality aggregates must not silently blend different local builds.  A
    # hosted API may not expose its server build, but each local method must be
    # one exact executable across all 150 tasks.
    for provider in names:
        local=[r for r in records if r['provider']==provider
               and r.get('provider_metadata',{}).get('deployment')=='local process']
        if local:
            require(len({r['provider_metadata']['binary_sha256'] for r in local})==1,
                    ('Local implementation changed inside report cohort',provider))
    batches=json.loads((root/'execution.json').read_text())['batches']
    require(Counter(i for b in batches for i in b['attempt_ids'])==Counter(r['attempt_id'] for r in records),'Execution attempt coverage differs')
    by_attempt={r['attempt_id']:r for r in records}
    for batch in batches:
        for provider in {by_attempt[i]['provider'] for i in batch['attempt_ids']}:
            group=[by_attempt[i] for i in batch['attempt_ids'] if by_attempt[i]['provider']==provider]
            hashes={r['provider_metadata'].get('binary_sha256') for r in group
                    if r['provider_metadata'].get('binary_sha256')}
            versions={r['provider_metadata'].get('browser_version') for r in group
                      if r['provider_metadata'].get('browser_version')}
            require(len(hashes)<=1 and len(versions)<=1,
                    ('Implementation changed inside execution batch',batch.get('batch_id'),provider))
    valid=[b for b in batches if b.get('reference_checks',{}).get('status','verified')=='verified']
    require_verified_reference_checks(valid,cases=dataset,records=records,source=root,
        equivalent_capture_bindings=dataset.get('comparison',{}).get('reference_binding_mode') ==
        'equivalent_reference_bytes_with_independent_batch_captures')
    failed=sorted({cid for b in batches for cid,row in b.get('reference_checks',{}).get('postcheck',{}).get('cases',{}).items() if row['status']!='verified'})
    require(failed==dataset['comparison']['unverified_reference_cases'],'Reference blocker inventory differs')
    print('PASS:',len(cases),'tasks,',len(records),'unique calls; hashes, rules, identities and available certificates verified')
    if rescore:print('PASS: all persisted metrics reproduce without changing outputs or scores')
    print('Publication reference blockers:',failed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,nargs='?',default=ROOT/'evaluation/headless')
    parser.add_argument('--rescore',action='store_true')
    args=parser.parse_args();check(args.source,args.rescore)
