"""Build a bound same-window latency comparison from a complete headless subcohort."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.metrics import percentile, task_passed
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.result_review import require_result_review


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();root=args.run
    dataset=json.loads((root/'dataset.json').read_text())
    cases={c['id']:c for c in dataset['cases']}
    records=[json.loads(line) for line in (root/'results.jsonl').read_text().splitlines()]
    manifest=json.loads((root/'manifest.json').read_text())
    require_verified_reference_checks(manifest,cases=dataset,records=records,source=root)
    require_result_review(root,dataset,records)
    providers=sorted({r['provider'] for r in records})
    expected={(provider,cid) for provider in providers for cid in cases}
    if len(records)!=len(expected) or {(r['provider'],r['case_id']) for r in records}!=expected:
        raise SystemExit('Speed cohort must contain exactly one complete provider/case matrix')
    passed={(r['provider'],r['case_id']):task_passed(r['ok'],r['metrics'],cases[r['case_id']].get('task_kind','body')) for r in records}
    common=sorted(cid for cid in cases if all(passed[p,cid] for p in providers))
    if len(common)<max(5,len(cases)//2):
        raise SystemExit('Too few all-provider successful cases for a defensible latency comparison')
    rows=[]
    for provider in providers:
        values=[r['elapsed_ms'] for r in records if r['provider']==provider and r['case_id'] in common]
        rows.append({'provider':provider,'attempts':len(values),'p50_ms':statistics.median(values),
                     'p95_ms':percentile(values,.95),'values_ms':values})
    summary={'schema_version':1,'selection':'Predeclared ordinary-web body cases stratified by reference length; latency uses the single all-provider successful case intersection.',
             'bindings':{'dataset_sha256':hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest(),
                         'results_sha256':hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()},
             'providers':providers,'planned_cases':sorted(cases),'common_pass_cases':common,
             'started_at':manifest['started_at'],'completed_at':manifest['completed_at'],'rows':rows}
    (root/'speed-comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(f"common-pass={len(common)}/{len(cases)} providers={len(providers)}")


if __name__=='__main__':main()
