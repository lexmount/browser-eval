"""Assemble exact headless measurements, never selecting retries by score."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks, requires_fresh_reference
from harness.web_fetch.safety import contained_path, validate_report_ids


def read_records(root):
    return [json.loads(line) for line in (root/'results.jsonl').read_text().splitlines()]


def public_manifest(manifest):
    result=deepcopy(manifest)
    result.get('environment',{}).pop('hostname',None)
    result.setdefault('dataset',{})['source']='evaluation/headless/dataset.json (assembled frozen cases)'
    result['provider_config_source']='configs/providers.headless.json'
    if 'projection' in result:
        result['projection'].pop('source_manifest_path',None)
        result['projection']['source_manifest_retained_in_local_run']=True
        if result['projection']['kind']=='completed-case-from-live-preflight':
            result['dataset']['case_count']=len(result['reference_checks']['cases'])
    return result


def assemble(run, certificates, replacements, output, retain_unverified=False):
    if output.exists():raise ValueError('Destination exists; evidence must not be overwritten')
    dataset=json.loads((run/'dataset.json').read_text())
    cases={c['id']:c for c in dataset['cases']}
    original=read_records(run)
    expected={(c['id'],p) for c in cases.values() for p in dataset['coverage_plan'][c.get('track','web')]}
    if len(original)!=len(expected) or {(r['case_id'],r['provider']) for r in original}!=expected:
        raise ValueError('Original run has incomplete coverage')
    replacement_cases={};batches=[];evidence=[];selected=[];audit=[]
    for root in replacements:
        new_dataset=json.loads((root/'dataset.json').read_text())
        rows=read_records(root)
        manifest=json.loads((root/'manifest.json').read_text())
        require_verified_reference_checks(manifest,cases=new_dataset,records=rows,source=root)
        for case in new_dataset['cases']:
            cid=case['id']
            if cid not in cases or cid in replacement_cases:raise ValueError('Unknown/duplicate replacement case')
            before,after=deepcopy(cases[cid]),deepcopy(case)
            for value in (before,after):
                value.pop('source_verification',None);value.pop('ground_truth_main_content',None)
            if before!=after:raise ValueError('Replacement changed task rules: '+cid)
            replacement_cases[cid]=(case,root)
            audit.append({'case_id':cid,'selection_reason':'fresh complete matched cohort after failed reference certificate; no score-based selection',
                'excluded_attempts':[{k:r[k] for k in ('attempt_id','run_id','started_at','elapsed_ms')} for r in original if r['case_id']==cid]})
        batches.append(public_manifest(manifest));evidence.append(root)
        selected.extend((r,root) for r in rows)
    for cid,case in cases.items():
        if cid in replacement_cases:continue
        rows=[r for r in original if r['case_id']==cid]
        if requires_fresh_reference(case):
            cert=certificates/cid
            manifest=json.loads((cert/'manifest.json').read_text())
            if {r['attempt_id']:r for r in read_records(cert)}!={r['attempt_id']:r for r in rows}:
                raise ValueError('Certificate records differ from original call records: '+cid)
            batches.append(public_manifest(manifest));evidence.append(cert)
        selected.extend((r,run) for r in rows)
    stable=[r for r in original if not requires_fresh_reference(cases[r['case_id']]) and r['case_id'] not in replacement_cases]
    if stable:
        manifest=json.loads((run/'manifest.json').read_text())
        manifest.pop('reference_checks',None)  # only non-snapshot cases in this projection
        manifest.update(attempt_count=len(stable),attempt_ids=[r['attempt_id'] for r in stable])
        manifest['dataset']['case_count']=len({r['case_id'] for r in stable})
        manifest['projection']={'kind':'non-snapshot-cases-only','source_manifest_sha256':hashlib.sha256((run/'manifest.json').read_bytes()).hexdigest()}
        batches.append(public_manifest(manifest))
    dataset['cases']=[replacement_cases.get(cid,(case,None))[0] for cid,case in cases.items()]
    records=[r for r,_ in selected]
    validate_report_ids(dataset,records)
    if len(records)!=len(expected) or {(r['case_id'],r['provider']) for r in records}!=expected:
        raise ValueError('Assembled coverage differs from the complete cohort')
    failed=[cid for batch in batches for cid,row in batch.get('reference_checks',{}).get('postcheck',{}).get('cases',{}).items() if row['status']!='verified']
    dataset['comparison']['publication_state']='blocked_reference_checks' if failed else 'verified'
    dataset['comparison']['unverified_reference_cases']=sorted(set(failed))
    if not retain_unverified:
        require_verified_reference_checks(batches,cases=dataset,records=records)
    output.mkdir(parents=True)
    for folder in ('sources','list-references'):
        shutil.copytree(run/folder,output/folder)
    for cid,(_,root) in replacement_cases.items():
        # This is a newly created assembly tree, not previously measured output.
        target=output/'sources'/cid
        shutil.copytree(root/'sources'/cid,target,dirs_exist_ok=True)
    for row,root in selected:
        paths=[(row['artifact_path'],row['metrics']['content_sha256'])]
        meta=row.get('provider_metadata',{})
        for path_key,hash_key in [('rendered_html_path','rendered_html_sha256'),('raw_stdout_path','raw_stdout_sha256')]:
            if meta.get(path_key):paths.append((meta[path_key],meta[hash_key]))
        for relative,digest in paths:
            source=contained_path(root,relative)
            if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:raise ValueError('Artifact changed: '+relative)
            target=contained_path(output,relative);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    for root in evidence:
        if (root/'reference-checks').exists():
            shutil.copytree(root/'reference-checks',output/'reference-checks',dirs_exist_ok=True)
    # Keep rejected calls, scores and certificates available outside rankings.
    for cid in replacement_cases:
        target=output/'diagnostics'/'replaced-cases'/cid;target.mkdir(parents=True)
        rows=[r for r in original if r['case_id']==cid]
        (target/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        rejected=public_manifest(json.loads((certificates/cid/'manifest.json').read_text()))
        (target/'manifest.json').write_text(json.dumps(rejected,ensure_ascii=False,indent=2)+'\n')
        shutil.copytree(certificates/cid/'reference-checks',target/'reference-checks')
        for row in rows:
            meta=row.get('provider_metadata',{})
            for relative in filter(None,[row['artifact_path'],meta.get('rendered_html_path'),meta.get('raw_stdout_path')]):
                dest=contained_path(target,relative);dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(contained_path(run,relative),dest)
    for name,payload in [('dataset.json',dataset),('execution.json',{'batches':batches}),('selection-audit.json',audit)]:
        (output/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    (output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    if not failed:require_verified_reference_checks(batches,cases=dataset,records=records,source=output)
    print('Assembled',len(records),'calls; publication:',dataset['comparison']['publication_state'],'unverified:',failed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--certificates',type=Path,required=True)
    parser.add_argument('--replacements',type=Path,nargs='*',default=[])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--retain-unverified',action='store_true',help='Retain a blocked diagnostic cohort; the report builder will still reject it')
    args=parser.parse_args()
    assemble(args.run_dir,args.certificates,args.replacements,args.output,args.retain_unverified)
