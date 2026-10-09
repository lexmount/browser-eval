"""Build a bound same-window latency comparison from a complete headless subcohort."""
import argparse
import hashlib
import json
import math
import statistics
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from harness.record_io import load_jsonl
from harness.web_fetch.artifacts import materialize_task_references
from harness.web_fetch.metrics import percentile, task_passed
from harness.web_fetch.reference_inputs import input_tree, read_dataset_bytes
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks


def _timestamp(value):
    if not isinstance(value,str):
        raise ValueError('Speed batch timestamps must be ISO 8601 strings')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('Speed batch timestamps require an explicit timezone')
    return parsed


def provider_cohort(dataset):
    tracks={case.get('track','web') for case in dataset.get('cases',[])}
    coverage=dataset.get('coverage_plan',{})
    cohorts=[coverage.get(track) for track in tracks]
    if (not tracks or any(not isinstance(names,list) or not names for names in cohorts)
            or any(names != cohorts[0] for names in cohorts[1:])
            or len(cohorts[0]) != len(set(cohorts[0]))):
        raise ValueError('Comparison dataset has no single authoritative provider cohort')
    return cohorts[0]


def derive_aggregate(dataset,batches):
    """Derive only envelope metadata that is provable from existing batches."""
    if not isinstance(batches,list) or not batches:
        raise ValueError('Legacy speed execution has no batches to upgrade')
    starts=[_timestamp(batch.get('started_at')) for batch in batches]
    finishes=[_timestamp(batch.get('completed_at')) for batch in batches]
    if any(end < start for start,end in zip(starts,finishes)):
        raise ValueError('Legacy speed execution contains an invalid batch interval')
    return {
        'started_at':min(starts).isoformat(),
        'completed_at':max(finishes).isoformat(),
        'providers':provider_cohort(dataset),
        'case_ids':[case['id'] for case in dataset.get('cases',[])],
        'batch_count':len(batches),
    }


def build_summary(dataset, records, batches, aggregate):
    cases={c['id']:c for c in dataset['cases']}
    if len(cases)!=len(dataset['cases']):
        raise ValueError('Speed dataset contains duplicate cases')
    providers=provider_cohort(dataset)
    expected={(provider,cid) for provider in providers for cid in cases}
    if len(records)!=len(expected) or {(r['provider'],r['case_id']) for r in records}!=expected:
        raise ValueError('Speed cohort must contain exactly one complete provider/case matrix')
    expected_attempts={f'{provider}__{cid}__r001' for provider,cid in expected}
    if ({r.get('attempt_id') for r in records} != expected_attempts
            or any(r.get('run') != 1 for r in records)):
        raise ValueError('Speed cohort must contain canonical single-run attempt identities')
    if not isinstance(batches,list) or len(batches)!=len(providers):
        raise ValueError('Speed cohort requires exactly one batch per provider')
    batch_by_provider={}
    for batch in batches:
        names=[row.get('name') for row in batch.get('providers',[]) if isinstance(row,dict)]
        if len(names)!=1 or names[0] not in providers or names[0] in batch_by_provider:
            raise ValueError('Speed batch does not bind exactly one authoritative provider')
        provider=names[0]
        attempt_ids=batch.get('attempt_ids')
        wanted={f'{provider}__{cid}__r001' for cid in cases}
        if (not isinstance(attempt_ids,list) or len(attempt_ids)!=len(wanted)
                or set(attempt_ids)!=wanted or batch.get('attempt_count')!=len(wanted)):
            raise ValueError('Speed provider batch does not cover its exact case cohort')
        batch_by_provider[provider]=batch
    starts=[_timestamp(batch['started_at']) for batch in batches]
    finishes=[_timestamp(batch['completed_at']) for batch in batches]
    if any(end < start for start,end in zip(starts,finishes)) or max(starts)>min(finishes):
        raise ValueError('Speed provider batches have no common execution window')
    if (not isinstance(aggregate,dict) or aggregate.get('providers')!=providers
            or aggregate.get('case_ids')!=list(cases)
            or aggregate.get('batch_count')!=len(batches)):
        raise ValueError('Speed aggregate execution identity differs from its dataset')
    aggregate_start=_timestamp(aggregate.get('started_at'))
    aggregate_end=_timestamp(aggregate.get('completed_at'))
    if (aggregate_end < aggregate_start or min(starts)<aggregate_start
            or max(finishes)>aggregate_end):
        raise ValueError('Speed batches fall outside aggregate execution bounds')
    for record in records:
        elapsed=record.get('elapsed_ms')
        if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:
            raise ValueError('Speed cohort contains invalid elapsed time')
    passed={(r['provider'],r['case_id']):task_passed(
        r['ok'],r['metrics'],cases[r['case_id']].get('task_kind','body')) for r in records}
    common=sorted(cid for cid in cases if all(passed[p,cid] for p in providers))
    rows=[]
    for provider in providers:
        provider_rows=[r for r in records if r['provider']==provider]
        successful=[r for r in provider_rows if passed[provider,r['case_id']]]
        all_values=[r['elapsed_ms'] for r in provider_rows]
        success_values=[r['elapsed_ms'] for r in successful]
        rows.append({
            'provider':provider,
            'planned_attempts':len(provider_rows),
            'successful_attempts':len(successful),
            'task_pass_rate':len(successful)/len(provider_rows),
            'all_calls_p50_ms':statistics.median(all_values),
            'all_calls_p95_ms':percentile(all_values,.95),
            'successful_calls_p50_ms':statistics.median(success_values) if success_values else None,
            'successful_calls_p95_ms':percentile(success_values,.95) if success_values else None,
        })
    return {
        'schema_version':2,
        'selection':'Predeclared ordinary-web body cases stratified by reference length and unique hostname; every method uses the same full task sample.',
        'interpretation':'descriptive_single_window_single_observation',
        'latency_scopes':{
            'all_calls':'Same task denominator for every method; includes successful delivery, failures, and deadline timeouts.',
            'successful_calls':'Conditioned on each method task pass; denominators may differ and are not a matched-task ranking.',
            'common_pass_cases':'Diagnostic only; never used to select or discard the predeclared cohort.',
        },
        'percentile_limit':f"Each method has {len(cases)} single observations. P95 is a sample diagnostic, not a stable tail-latency estimate or SLA.",
        'providers':providers,'planned_cases':list(cases),'common_pass_cases':common,
        'started_at':min(starts).isoformat(),
        'completed_at':max(finishes).isoformat(),'rows':rows,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--upgrade-execution-metadata',action='store_true',help='Upgrade a legacy batches-only execution.json after validating its existing evidence; never calls providers')
    args=parser.parse_args(argv);root=args.run
    dataset=json.loads((root/'dataset.json').read_text())
    cases={c['id']:c for c in dataset['cases']}
    records=load_jsonl(root/'results.jsonl')
    execution_path=root/'execution.json'
    if not execution_path.is_file():
        raise ValueError('Speed cohort requires aggregate execution.json')
    execution=json.loads(execution_path.read_text())
    batches=execution.get('batches')
    upgrade=False
    if execution.get('schema_version') == 1 and isinstance(execution.get('aggregate'),dict):
        aggregate=execution['aggregate']
    elif (args.upgrade_execution_metadata and execution.get('schema_version') is None
          and execution.get('aggregate') is None):
        aggregate=derive_aggregate(dataset,batches)
        upgrade=True
    else:
        raise ValueError('Unsupported speed execution schema; use --upgrade-execution-metadata only for a legacy batches-only manifest')
    require_verified_reference_checks(batches,cases=dataset,records=records,source=root,
        allow_scoring_rule_revision=True)
    summary=build_summary(dataset,records,batches,aggregate)
    if upgrade:
        upgraded={**execution,'schema_version':1,'aggregate':aggregate}
        temporary=execution_path.with_suffix('.json.upgrading')
        temporary.write_text(json.dumps(upgraded,ensure_ascii=False,indent=2)+'\n')
        temporary.replace(execution_path)
    summary['bindings']={'dataset_sha256':hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest(),
                         'results_sha256':hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()}
    (root/'speed-comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(f"common-pass={len(summary['common_pass_cases'])}/{len(cases)} providers={len(summary['providers'])}; full predeclared sample retained")


def select_cases(cases, count, excluded=frozenset()):
    eligible=[c for c in cases if c.get('track','web')=='web'
              and c.get('task_kind','body')=='body' and c.get('ground_truth_main_content')
              and c['id'] not in excluded]
    if type(count) is not int or count <= 0 or count%5 or len(eligible)<count:
        raise ValueError('Count must be a feasible multiple of five')
    eligible.sort(key=lambda c:(len(c['ground_truth_main_content']),c['id']))
    selected=[]; used_hosts=set()
    for index in range(count):
        target=round((index+.5)*len(eligible)/count-.5)
        candidates=[(abs(position-target),position,case['id'],case)
                    for position,case in enumerate(eligible)
                    if (urlsplit(case['url']).hostname or '') not in used_hosts]
        if not candidates:
            raise ValueError('Not enough distinct hosts for the requested speed cohort')
        case=min(candidates)[-1]
        selected.append(case);used_hosts.add(urlsplit(case['url']).hostname or '')
    return selected


def selection_main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--count',type=int,default=20)
    parser.add_argument('--exclude-case-ids',default='',
                        help='Provider-blind preflight reference exclusions')
    args=parser.parse_args()
    if args.output.exists():parser.error('Destination exists')
    dataset=json.loads(read_dataset_bytes(args.dataset))
    try:
        selected=select_cases(dataset['cases'],args.count,
                              set(filter(None,args.exclude_case_ids.split(','))))
    except ValueError as error:
        parser.error(str(error))
    result=deepcopy(dataset);result['cases']=selected
    from .config import derive_comparison
    result['comparison']=derive_comparison(dataset, {'kind':'headless_same_window_speed',
        'selection':'Provider-blind reference-length quantile sampling over ordinary web body tasks; at most one case per hostname',
        'planned_before_calls':True,'case_count':len(selected)})
    args.output.mkdir(parents=True)
    (args.output/'dataset.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    materialize_task_references(args.output, {name: input_tree(args.dataset,name)
        for name in ('sources','list-references')}, [case['id'] for case in selected])
    print(','.join(c['id'] for c in selected))
