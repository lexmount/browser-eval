"""Run the current task set with per-track provider settings, then build a report."""
import argparse
import hashlib
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.config import load_dataset,load_provider_config,load_runtime_env
from harness.web_fetch.providers import build_provider
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.report_build import build_report
from harness.web_fetch.safety import contained_path
from harness.web_fetch.storage import data_home


def artifact_bindings(record):
    bindings = {}
    def add(relative, digest):
        if relative in bindings and bindings[relative] != digest:
            raise ValueError('Artifact has conflicting hash bindings: ' + relative)
        bindings[relative] = digest

    add(record['artifact_path'], record['metrics']['content_sha256'])
    metadata = record.get('provider_metadata', {})
    for path_key, hash_key in (('rendered_html_path', 'rendered_html_sha256'),
                               ('raw_stdout_path', 'raw_stdout_sha256')):
        if metadata.get(path_key):
            add(metadata[path_key], metadata[hash_key])
    for item in metadata.get('file_evaluation', {}).get('files', []):
        if item.get('artifact_path'):
            add(item['artifact_path'], item['sha256'])
    for attempt in metadata.get('retry', {}).get('attempts', []):
        hashes = attempt.get('artifact_sha256', {})
        for relative in attempt.get('artifact_paths', []):
            digest = hashes.get(relative)
            if relative == attempt.get('artifact_path'):
                digest = digest or attempt.get('content_sha256')
            if not digest:
                raise ValueError('Retry artifact has no hash binding: ' + relative)
            add(relative, digest)
    return list(bindings.items())


def bind_retry_artifacts(worker, records):
    """Bind auxiliary physical-call evidence before leaving the worker tree."""
    for record in records:
        retry = record.get('provider_metadata', {}).get('retry', {})
        for attempt in retry.get('attempts', []):
            hashes = attempt.setdefault('artifact_sha256', {})
            for relative in attempt.get('artifact_paths', []):
                source = contained_path(worker, relative)
                if not source.is_file():
                    raise ValueError('Retry artifact is missing: ' + relative)
                hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()


def materialize_worker_artifacts(output, worker, records):
    """Copy every hash-bound deliverable from a worker into the aggregate root."""
    bind_retry_artifacts(worker, records)
    for record in records:
        for relative, digest in artifact_bindings(record):
            source = contained_path(worker, relative)
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise ValueError('Worker artifact differs from its result binding: ' + relative)
            target = contained_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError('Aggregate artifact collision: ' + relative)
            else:
                shutil.copy2(source, target)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'evaluation/dataset.json',help='Frozen dataset JSON; sources and list-references are resolved beside it')
    parser.add_argument('--output',type=Path,default=data_home()/'runs/webfetch')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--providers',help='Comma-separated names; default: all benchmark methods')
    parser.add_argument('--track',help='Run only this task group, such as dynamic; default: all groups')
    parser.add_argument('--case-ids',help='Comma-separated case IDs; default: every case in the selected task group')
    parser.add_argument('--validate-only',action='store_true',help='Check selected configuration without creating files or calling providers')
    parser.add_argument('--measure-only',action='store_true',help='Persist measured evidence without building an unreviewed report')
    parser.add_argument('--workers',type=int,default=11)
    args=parser.parse_args(argv)
    if args.workers < 1:parser.error('--workers must be positive')
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
    selected_tracks={case.track for case in cases}
    planned={name for track in selected_tracks for name in dataset.get('coverage_plan',{}).get(track,[])}
    names=[n.strip() for n in args.providers.split(',') if n.strip()] if args.providers else sorted(planned)
    if not args.providers and not names:
        parser.error('Dataset has no provider coverage plan; pass --providers explicitly')
    if not names or len(names)!=len(set(names)):parser.error('Select nonempty, unique provider names')
    dataset={**dataset,'coverage_plan':{c.track:sorted(names) for c in cases}}
    scopes=[]
    for scope,filename in [('web','providers.example.json'),('resources','providers.resources.json')]:
        selected=[c for c in cases if (c.track=='web')==(scope=='web')]
        if selected:
            path=ROOT/'configs'/filename
            scopes.append((scope,path,load_provider_config(path),selected))
    errors=[]
    for scope,path,config,selected in scopes:
        for name in names:
            if name not in config:
                errors.append(f'{scope}/{name}: unknown provider')
                continue
            missing=build_provider(name,config[name]).validate()
            if missing:errors.append(f'{scope}/{name}: missing {", ".join(missing)}')
    if errors:parser.error('\n'.join(errors))
    if args.validate_only:
        print(f'Configuration valid: {len(cases)} tasks × {len(names)} methods; no provider calls made')
        return
    args.output.mkdir(parents=True)
    def work(name):
        batches=[];records=[]
        for scope,path,config,selected in scopes:
            meta={**dataset,'cases':[c for c in dataset['cases'] if (c.get('track','web')=='web')==(scope=='web')]}
            folder=args.output/'workers'/name/scope
            provider=build_provider(name,config[name])
            rs,_=run_evaluation(output_dir=folder,dataset_meta=meta,dataset_path=args.dataset,cases=selected,providers=[provider],provider_config_path=path,runs=1,timeout=45,delay=.5,provider_concurrency=1)
            materialize_worker_artifacts(args.output, folder, rs)
            batches.append(json.loads((folder/'manifest.json').read_text()));records.extend(rs)
        return records,batches
    aggregate_started_at=datetime.now(UTC).isoformat()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:parts=list(pool.map(work,names))
    aggregate_completed_at=datetime.now(UTC).isoformat()
    records=[r for rs,_ in parts for r in rs];batches=[b for _,bs in parts for b in bs]
    assert len(records)==len(cases)*len(names)
    execution={'schema_version':1,'aggregate':{
        'started_at':aggregate_started_at,'completed_at':aggregate_completed_at,
        'providers':sorted(names),'case_ids':[case.id for case in cases],'batch_count':len(batches),
    },'batches':batches}
    for name,value in [('dataset.json',dataset),('execution.json',execution)]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (args.output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    for case in cases:
        source=args.dataset.parent/'sources'/case.id
        if source.exists():shutil.copytree(source,args.output/'sources'/case.id)
        reference=args.dataset.parent/'list-references'/(case.id+'.json')
        if reference.exists():
            (args.output/'list-references').mkdir(exist_ok=True)
            shutil.copy2(reference,args.output/'list-references'/reference.name)
    if args.measure_only:
        print(args.output)
    else:
        print(build_report(args.output,args.output/'report'))

if __name__=='__main__':main()
