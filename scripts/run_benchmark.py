"""Run the current task set with per-track provider settings, then build a report."""
import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from browser_harness_eval.config import load_dataset,load_provider_config,load_env_file
from browser_harness_eval.providers import build_provider
from browser_harness_eval.runner import run_evaluation
from browser_harness_eval.report_build import build_report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'runs/webfetch')
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=11)
    args=parser.parse_args()
    if args.output.exists():parser.error('Output exists; choose an empty directory to avoid overwriting measured evidence')
    load_env_file(args.env_file)
    dataset,cases=load_dataset(ROOT/'evaluation/dataset.json')
    paths=[ROOT/'configs/providers.example.json',ROOT/'configs/providers.resources.json']
    configs=[load_provider_config(p) for p in paths]
    names=sorted({json.loads(line)['provider'] for line in (ROOT/'evaluation/results.jsonl').read_text().splitlines()})
    args.output.mkdir(parents=True)
    def work(name):
        batches=[];records=[]
        for scope,path,config in zip(['web','resources'],paths,configs):
            selected=[c for c in cases if (c.track=='web')==(scope=='web')]
            meta={**dataset,'cases':[c for c in dataset['cases'] if (c.get('track','web')=='web')==(scope=='web')]}
            folder=args.output/'workers'/name/scope
            provider=build_provider(name,config[name])
            rs,_=run_evaluation(output_dir=folder,dataset_meta=meta,dataset_path=ROOT/'evaluation/dataset.json',cases=selected,providers=[provider],provider_config_path=path,runs=1,timeout=45,delay=.5,provider_concurrency=1)
            for r in rs:
                target=args.output/r['artifact_path'];target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(folder/r['artifact_path'],target)
            batches.append(json.loads((folder/'manifest.json').read_text()));records.extend(rs)
        return records,batches
    with ThreadPoolExecutor(max_workers=args.workers) as pool:parts=list(pool.map(work,names))
    records=[r for rs,_ in parts for r in rs];batches=[b for _,bs in parts for b in bs]
    assert len(records)==len(cases)*len(names)
    for name,value in [('dataset.json',dataset),('execution.json',{'batches':batches})]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (args.output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    shutil.copytree(ROOT/'evaluation/sources',args.output/'sources')
    shutil.copytree(ROOT/'evaluation/list-references',args.output/'list-references')
    print(build_report(args.output,args.output/'report'))

if __name__=='__main__':main()
