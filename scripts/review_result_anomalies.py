"""Investigate anomalies after an already-recorded blind result review."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.anomaly_review import LAYERS, TRIGGER_VERSION, detect_anomalies
from harness.web_fetch.result_review import require_blind_result_review, require_result_review
from scripts.review_headless_results import anomaly_investigation_packet, chunks


LAYER_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    'status':{'enum':['verified','defect','not_applicable','insufficient']},
    'evidence':{'type':'string'}},'required':['status','evidence']}
GROUP_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    'groups':{'type':'array','items':{'type':'object','additionalProperties':False,
        'properties':{
            'group_id':{'type':'string'}, 'attempt_ids':{'type':'array','items':{'type':'string'}},
            'layers':{'type':'object','additionalProperties':False,
                      'properties':{layer:LAYER_SCHEMA for layer in LAYERS},
                      'required':list(LAYERS)},
            'same_class_rationale':{'type':'string'}, 'root_cause':{'type':'string'},
            'corrective_action':{'type':'string'},
            'status':{'enum':['no-defect','corrected','unresolved']},
            'evaluation_defect':{'type':'boolean'}, 'correction_applied':{'type':'boolean'},
            'rerun_required':{'type':'boolean'},
            'rerun_attempt_ids':{'type':'array','items':{'type':'string'}},
            'rerun_reason':{'type':'string'}},
        'required':['group_id','attempt_ids','layers','same_class_rationale','root_cause',
                    'corrective_action','status','evaluation_defect','correction_applied',
                    'rerun_required','rerun_attempt_ids','rerun_reason']}},
    }, 'required':['groups']}


def anomaly_groups(dataset, records, triggers):
    cases={case['id']:case for case in dataset['cases']}
    grouped={}
    for record in records:
        aid=record['attempt_id']
        if aid not in triggers:
            continue
        metadata=record.get('provider_metadata',{})
        status=record.get('status_code')
        status_family=(status//100 if isinstance(status,int) else None)
        key=(record.get('provider'),cases[record['case_id']].get('track'),
             metadata.get('route'),record.get('error_type'),status_family,
             metadata.get('document_response'),tuple(triggers[aid]))
        grouped.setdefault(key,[]).append(aid)
    result=[]
    for index, (_, attempt_ids) in enumerate(sorted(grouped.items(),key=lambda item:str(item[0])),1):
        result.append({'group_id':f'anomaly-group-{index:03d}',
                       'attempt_ids':sorted(attempt_ids)})
    return result


def merge_group_resolutions(prior, groups, resolutions, triggers):
    expected={group['group_id']:group['attempt_ids'] for group in groups}
    rows=resolutions.get('groups',[])
    if len(rows)!=len(expected) or {row.get('group_id') for row in rows}!=set(expected):
        raise ValueError('Anomaly resolution must cover every group exactly once')
    audits={}
    for row in rows:
        attempt_ids=row.get('attempt_ids',[])
        if attempt_ids!=expected[row['group_id']]:
            raise ValueError('Anomaly group attempt scope changed')
        rerun_ids=row.get('rerun_attempt_ids',[])
        for aid in attempt_ids:
            audits[aid]={
                'triggers':triggers[aid], 'layers':copy.deepcopy(row['layers']),
                'same_class_scope':{'attempt_ids':attempt_ids,
                                    'rationale':row['same_class_rationale']},
                'root_cause':row['root_cause'],'corrective_action':row['corrective_action'],
                'status':row['status'],'evaluation_defect':row['evaluation_defect'],
                'correction_applied':row['correction_applied'],
                'rerun_scope':{'required':row['rerun_required'],'attempt_ids':rerun_ids,
                               'reason':row['rerun_reason']}}
    merged=copy.deepcopy(prior)
    for row in merged['attempts']:
        row.setdefault('reviewer_anomalies',[])
        if row['attempt_id'] in audits:
            row['anomaly_review']=audits[row['attempt_id']]
        else:
            row.pop('anomaly_review',None)
    merged.setdefault('review_protocol',{})['anomaly_review']={
        'trigger_version':TRIGGER_VERSION,'performed_after_blind_verdict':True,
        'seven_layers':list(LAYERS),'triggered_attempts':len(triggers),
        'grouped_same_class_investigation':True}
    return merged


def validate_group_batch(groups, result):
    expected={group['group_id']:group['attempt_ids'] for group in groups}
    rows=result.get('groups',[]) if isinstance(result,dict) else []
    if len(rows)!=len(expected) or {row.get('group_id') for row in rows}!=set(expected):
        raise ValueError('Anomaly batch must cover every group exactly once')
    for row in rows:
        if row.get('attempt_ids')!=expected[row['group_id']]:
            raise ValueError('Anomaly batch changed a group attempt scope')
    return result


def cached_group_resolutions(work: Path, groups: list[dict]) -> tuple[list[dict], list[dict]]:
    """Reuse completed groups by exact attempt scope, not positional batch number.

    Group numbering and batch boundaries can move when one attempt's triggers or
    reviewer anomalies change.  A positional cache would then either replay a
    stale resolution against another group or force every completed batch to be
    rerun.  Exact attempt scope is the stable investigation identity; remap its
    presentation-only group id to the current inventory.
    """
    current = {tuple(group['attempt_ids']): group for group in groups}
    cached: dict[tuple[str, ...], dict] = {}
    for path in sorted(work.glob('anomaly-*.json')):
        if any(token in path.name for token in ('-input.json', '.raw.json',
                                                '.superseded.json')):
            continue
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for row in payload.get('groups', []) if isinstance(payload, dict) else []:
            scope = tuple(row.get('attempt_ids', []))
            group = current.get(scope)
            if not group:
                continue
            remapped = copy.deepcopy(row)
            remapped['group_id'] = group['group_id']
            try:
                validate_group_batch([group], {'groups': [remapped]})
            except ValueError:
                continue
            cached[scope] = remapped
    pending = [group for group in groups if tuple(group['attempt_ids']) not in cached]
    return list(cached.values()), pending


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--reviewer',choices=('codex','internal'),default='codex')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--groups-per-batch',type=int,default=5)
    parser.add_argument('--timeout-seconds',type=int,default=900)
    parser.add_argument('--protocol-retries',type=int,default=2)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--install',action='store_true')
    args=parser.parse_args()
    evidence=args.evidence.resolve();work=args.work.resolve();work.mkdir(parents=True,exist_ok=True)
    dataset=json.loads((evidence/'dataset.json').read_text())
    records=[json.loads(line) for line in (evidence/'results.jsonl').read_text().splitlines()]
    prior=json.loads((evidence/'result-review.json').read_text())
    require_blind_result_review(evidence,dataset,records,prior)
    reviewer={row['attempt_id']:row.get('reviewer_anomalies',[]) for row in prior['attempts']}
    triggers=detect_anomalies(evidence,records,dataset,reviewer)
    groups=anomaly_groups(dataset,records,triggers)
    packet=anomaly_investigation_packet(evidence,dataset,records,prior['attempts'],triggers)
    attempts={row['attempt_id']:row for row in packet['attempts']}
    schema_path=work/'anomaly-schema.json';schema_path.write_text(json.dumps(GROUP_SCHEMA,indent=2)+'\n')
    reused,pending=cached_group_resolutions(work,groups)
    jobs=[]
    for index,batch in enumerate(chunks(pending,args.groups_per_batch),1):
        input_path=work/f'anomaly-pending-{index:03d}-input.json'
        output_path=work/f'anomaly-pending-{index:03d}.json'
        input_path.write_text(json.dumps({**packet,'groups':batch,
            'attempts':[attempts[aid] for group in batch for aid in group['attempt_ids']]},
            ensure_ascii=False,indent=2)+'\n')
        jobs.append((input_path,output_path,batch))
    if args.prepare_only:
        print(f'prepared={len(jobs)} groups={len(groups)} reused={len(reused)} '
              f'pending={len(pending)} work={work}')
        return
    def run(job):
        input_path,output_path,batch=job
        if output_path.is_file():
            return validate_group_batch(batch,json.loads(output_path.read_text()))
        if args.reviewer=='internal':
            raise SystemExit(f'Write post-verdict anomaly investigation output: {output_path}')
        raw=output_path.with_suffix('.raw.json')
        prompt=('Investigate the post-blind anomaly packet at '+str(input_path)+'. '
                'Return one resolution per declared group using the schema. Shared findings '
                'are allowed only for the exact group attempt_ids. Use read-only inspection; '
                'never change the recorded blind verdict or invent a correction. Follow the '
                'packet metric and layer definitions exactly. Outer API transport, target/origin '
                'status, provider-owned outcome, and task content are separate: empty target-2xx '
                'content is request success, while an explicit final non-2xx target/origin status '
                'or provider failure remains request failure. A retained redirect status is not '
                'a final-origin failure when the provider explicitly succeeded and delivered the '
                'redirected answer. Do not redefine request success as '
                'task success, and do not require a separate raw artifact for routes whose saved '
                'answer is the first answer-bearing output. Treat correction_applied as true only '
                'when this packet already contains the corrected persisted evidence; a working-tree '
                'code change alone is not a historical-evidence correction. A complete-body '
                'reference explicitly recorded as unavailable can still be verified when independent '
                'evidence binds that unavailable state, stable task checks remain usable, and all '
                'full-body metrics remain unavailable/null; do not label that condition insufficient '
                'merely because no full body was frozen.')
        command=['codex','exec','--model',args.model,'--sandbox','read-only','--add-dir',
                 str(evidence),'--ephemeral','-c','model_reasoning_effort="high"',
                 '--output-schema',str(schema_path),'--output-last-message',str(raw),prompt]
        problem=''
        for _ in range(args.protocol_retries+1):
            try:
                completed=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,
                                         timeout=args.timeout_seconds)
                if completed.returncode:
                    problem=completed.stderr or completed.stdout
                    continue
                result=validate_group_batch(batch,json.loads(raw.read_text()))
                output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
                return result
            except (subprocess.TimeoutExpired,json.JSONDecodeError,ValueError) as error:
                problem=str(error)
        raise RuntimeError(problem)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outputs=list(pool.map(run,jobs))
    resolutions={'groups':reused+[row for output in outputs for row in output['groups']]}
    merged=merge_group_resolutions(prior,groups,resolutions,triggers)
    require_result_review(evidence,dataset,records,merged)
    target=work/'result-review.json';target.write_text(json.dumps(merged,ensure_ascii=False,indent=2)+'\n')
    if args.install:
        # Validation above is the sole installation gate; copy only its exact document.
        shutil.copy2(target,evidence/'result-review.json')
    print(target)


if __name__=='__main__':
    main()
