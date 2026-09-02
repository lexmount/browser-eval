"""Freeze and rerun all ten dynamic tasks, with independent reference verification."""
import json,os,shutil,sys
from dataclasses import replace
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from browser_harness_eval.config import load_dataset,load_provider_config,load_env_file
from browser_harness_eval.providers import build_provider
from browser_harness_eval.runner import run_evaluation
from browser_harness_eval.list_verification import verify_list

def main():
    out=ROOT/'runs/dynamic'
    if out.exists() and any(out.iterdir()):raise ValueError('Measured output already exists')
    out.mkdir(parents=True,exist_ok=True)
    load_env_file(Path('/Users/lianglinkai/.env'))
    os.environ.setdefault('OSS_PYTHON','/private/tmp/webfetch-oss-venv/bin/python')
    os.environ.setdefault('COWAGENT_REPO','/Users/lianglinkai/.codex/worktrees/8d3d/CowAgent')
    source=ROOT/'evaluation';dataset,cases=load_dataset(source/'dataset.json')
    selected=[replace(c,task_prompt='取得至少三个不同条目的名称和本站详情链接；核对返回的前三条名称与详情身份，不要求固定排名、价格或某一批条目。',source_benchmark={**c.source_benchmark,'verification_scope':'first_three_name_detail_pairs'}) for c in cases if c.task_kind=='structure']
    for row in dataset['cases']:
        if row.get('task_kind')=='structure':
            c=next(x for x in selected if x.id==row['id'])
            row.update(task_prompt=c.task_prompt,source_benchmark=c.source_benchmark)
    (source/'dataset.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+'\n')
    references={}
    for c in selected:
        p=source/'list-references'/f'{c.id}.json'
        if c.id in {'challenge-04','challenge-06'}:
            items=json.loads((source/'sources'/c.id/'items.json').read_text())
            ref={'case_id':c.id,'url':c.url,'checked_at':c.source_verification['checked_at'],'method':'独立保存的公开列表字段','items':items}
            p.write_text(json.dumps(ref,ensure_ascii=False,indent=2)+'\n')
        references[c.id]=json.loads(p.read_text())
    configpath=ROOT/'configs/providers.resources.json';config=load_provider_config(configpath)
    old=[json.loads(l) for l in (source/'results.jsonl').read_text().splitlines()]
    names=sorted({r['provider'] for r in old})
    providers=[build_provider(n,config[n]) for n in names]
    meta={**dataset,'cases':[c for c in dataset['cases'] if c.get('task_kind')=='structure']}
    records,_=run_evaluation(output_dir=out,dataset_meta=meta,dataset_path=source/'dataset.json',cases=selected,providers=providers,provider_config_path=configpath,runs=1,timeout=45,delay=.5,provider_concurrency=11)
    assert len(records)==110
    for r in records:
        c=next(c for c in selected if c.id==r['case_id'])
        raw=out/r['artifact_path']
        r['metrics'].update(verify_list(raw.read_text(),c.source_benchmark,c.url,references[c.id],r['ok']))
        target=source/r['artifact_path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(raw,target)
    current=[r for r in old if r['case_id'] not in references]+records
    (source/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in current))
    execution=json.loads((source/'execution.json').read_text());ids={r['attempt_id'] for r in old if r['case_id'] not in references}
    for b in execution['batches']:
        b['attempt_ids']=[i for i in b.get('attempt_ids',[]) if i in ids]
        b['attempt_count']=len(b['attempt_ids'])
    execution['batches']=[b for b in execution['batches'] if b['attempt_ids']]
    batch=json.loads((out/'manifest.json').read_text());batch['attempt_ids']=[r['attempt_id'] for r in records];execution['batches'].append(batch)
    (source/'execution.json').write_text(json.dumps(execution,ensure_ascii=False,indent=2)+'\n')
    print('Complete: 110 fresh dynamic attempts; canonical evidence updated')

if __name__=='__main__':main()
