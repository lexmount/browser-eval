"""Execute task-scoped provider cohorts through the shared evidence pipeline."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import materialize_bound_artifacts, materialize_task_references
from .config import load_dataset, load_provider_config, load_runtime_env, add_failure_retry_argument
from .providers import build_provider
from .reference_inputs import input_tree
from .report_build import build_report
from .runner import preflight_declared_capacity, run_evaluation
from .storage import data_home

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'evaluation/dataset.json',help='Frozen dataset JSON; sources and list-references use its bound external inputs or adjacent directories')
    parser.add_argument('--output',type=Path,default=data_home()/'runs/webfetch')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--provider-config',type=Path,
                        help='One provider configuration for all selected tasks; defaults to the repository task-group configurations')
    parser.add_argument('--providers',help='Comma-separated names; default: all benchmark methods')
    parser.add_argument('--track',help='Run only this task group, such as dynamic; default: all groups')
    parser.add_argument('--case-ids',help='Comma-separated case IDs; default: every case in the selected task group')
    parser.add_argument('--validate-only',action='store_true',help='Check selected configuration without creating files or calling providers')
    parser.add_argument('--measure-only',action='store_true',help='Persist measured evidence without building an unreviewed report')
    parser.add_argument('--workers',type=int,default=11)
    parser.add_argument('--call-order', choices=('provider', 'case-alternating'), default='provider',
                        help='Run serially by task with alternating method order, or use provider batches')
    parser.add_argument('--runs',type=int,default=1)
    parser.add_argument('--timeout', type=float, default=45,
                        help='Positive per-call deadline in seconds, retained in execution evidence')
    add_failure_retry_argument(parser)
    args=parser.parse_args(argv)
    import math
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be positive and finite')
    if args.provider_config and not args.provider_config.is_file():
        parser.error('--provider-config must be an existing file')
    if args.workers < 1:parser.error('--workers must be positive')
    if args.call_order == 'case-alternating' and (args.workers != 1 or not args.provider_config):
        parser.error('--call-order case-alternating requires --workers 1 and one explicit --provider-config')
    if args.runs < 1:parser.error('--runs must be positive')
    if not args.validate_only and args.output.exists():parser.error('Output exists; choose an empty directory to avoid overwriting measured evidence')
    load_runtime_env(args.env_file)
    dataset,cases=load_dataset(args.dataset)
    if args.track:
        cases=[c for c in cases if c.track==args.track]
        if not cases:parser.error(f'Unknown or empty task group: {args.track}')
        dataset={**dataset,'cases':[c for c in dataset['cases'] if c.get('track','web')==args.track]}
    if args.case_ids:
        requested=[value.strip() for value in args.case_ids.split(',') if value.strip()]
        if not requested or len(requested)!=len(set(requested)):parser.error('Select nonempty, unique case IDs')
        known={case.id for case in cases}
        unknown=sorted(set(requested)-known)
        if unknown:parser.error('Case IDs outside selected dataset/task group: '+', '.join(unknown))
        selected=set(requested)
        cases=[case for case in cases if case.id in selected]
        dataset={**dataset,'cases':[case for case in dataset['cases'] if case['id'] in selected]}
    sources = input_tree(args.dataset, 'sources')
    list_references = input_tree(args.dataset, 'list-references')
    selected_tracks={case.track for case in cases}
    planned={name for track in selected_tracks for name in dataset.get('coverage_plan',{}).get(track,[])}
    names=[n.strip() for n in args.providers.split(',') if n.strip()] if args.providers else sorted(planned)
    if not args.providers and not names:
        parser.error('Dataset has no provider coverage plan; pass --providers explicitly')
    if not names or len(names)!=len(set(names)):parser.error('Select nonempty, unique provider names')
    dataset={**dataset,'coverage_plan':{c.track:sorted(names) for c in cases}}
    scopes=[]
    if args.provider_config:
        scopes.append(('selected', args.provider_config, load_provider_config(args.provider_config), cases))
    else:
        for scope,filename in [('web','providers.example.json'),('resources','providers.resources.json')]:
            selected=[c for c in cases if (c.track=='web')==(scope=='web')]
            if selected:
                path=ROOT/'configs'/filename
                scopes.append((scope,path,load_provider_config(path),selected))
    errors=[]
    selected_providers=[]
    for scope,path,config,selected in scopes:
        for name in names:
            if name not in config:
                errors.append(f'{scope}/{name}: unknown provider')
                continue
            provider=build_provider(name,config[name])
            selected_providers.append(provider)
            missing=provider.validate()
            if missing:errors.append(f'{scope}/{name}: missing {", ".join(missing)}')
    if errors:parser.error('\n'.join(errors))
    from .network_preflight import preflight_guarded_providers
    try:
        network_audit = preflight_guarded_providers(selected_providers, cases)
    except ValueError as error:
        parser.error(str(error))
    if args.validate_only:
        print(f'Configuration valid: {len(cases)} tasks × {len(names)} methods; no provider calls made')
        return
    declared_calls=len(cases)*args.runs*(args.max_failure_retries+1)
    preflight_declared_capacity(
        selected_providers, declared_calls=declared_calls, timeout=10,
        cases=cases, runs=args.runs,
        max_failure_retries=args.max_failure_retries,
    )
    args.output.mkdir(parents=True)
    if network_audit is not None:
        (args.output / "network-preflight.json").write_text(json.dumps(network_audit, ensure_ascii=False, indent=2) + "\n")
    def work(name):
        batches=[];records=[]
        for scope,path,config,selected in scopes:
            selected_ids = {case.id for case in selected}
            meta={**dataset,'cases':[c for c in dataset['cases'] if c['id'] in selected_ids]}
            folder=args.output/'workers'/name/scope
            provider=build_provider(name,config[name])
            rs,_=run_evaluation(output_dir=folder,dataset_meta=meta,dataset_path=args.dataset,cases=selected,providers=[provider],provider_config_path=path,runs=args.runs,timeout=args.timeout,delay=.5,provider_concurrency=1,max_failure_retries=args.max_failure_retries)
            materialize_bound_artifacts(args.output, folder, rs)
            batches.append(json.loads((folder/'manifest.json').read_text()));records.extend(rs)
        return records,batches
    aggregate_started_at=datetime.now(UTC).isoformat()
    if args.call_order == 'case-alternating':
        folder = args.output / 'workers' / 'interleaved'
        records, _ = run_evaluation(
            output_dir=folder, dataset_meta=dataset, dataset_path=args.dataset,
            cases=cases, providers=selected_providers, provider_config_path=args.provider_config,
            runs=args.runs, timeout=args.timeout, delay=.5, provider_concurrency=1,
            max_failure_retries=args.max_failure_retries, call_order=args.call_order,
        )
        materialize_bound_artifacts(args.output, folder, records)
        parts = [(records, [json.loads((folder / 'manifest.json').read_text())])]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:parts=list(pool.map(work,names))
    aggregate_completed_at=datetime.now(UTC).isoformat()
    records=[r for rs,_ in parts for r in rs];batches=[b for _,bs in parts for b in bs]
    assert len(records)==len(cases)*len(names)*args.runs
    execution={'schema_version':1,'aggregate':{
        'started_at':aggregate_started_at,'completed_at':aggregate_completed_at,
        'providers':sorted(names),'case_ids':[case.id for case in cases],'batch_count':len(batches),
        'call_order': args.call_order,
    },'batches':batches}
    for name,value in [('dataset.json',dataset),('execution.json',execution)]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (args.output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    materialize_task_references(args.output, {'sources':sources,'list-references':list_references},
                                [case.id for case in cases])
    if args.measure_only:
        print(args.output)
    else:
        print(build_report(args.output,args.output/'report'))
