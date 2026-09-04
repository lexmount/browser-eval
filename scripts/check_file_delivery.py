"""Run a separate real HTTP byte-delivery control; never replace supplier calls."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.config import load_dataset, load_provider_config, load_runtime_env
from harness.web_fetch.providers import build_provider
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.storage import current_evidence_dir


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new directory; existing evidence is immutable')
    load_runtime_env()
    dataset,cases=load_dataset(ROOT/'evaluation/dataset.json')
    cases=[c for c in cases if c.task_kind=='file']
    config=load_provider_config(ROOT/'configs/providers.resources.json')
    names=sorted({json.loads(line)['provider'] for line in (current_evidence_dir()/'results.jsonl').read_text().splitlines()})
    readiness=[]
    for name in names:
        setting=config[name]
        missing=build_provider(name,setting).validate()
        if name in {'openclaw-built-in','deepseek-harness-built-in'} and not setting.get('repo_path'):
            missing.append('repo_path')
        if name in {'langchain-webbase','crawl4ai'}:
            module='langchain_community' if name=='langchain-webbase' else 'crawl4ai'
            if importlib.util.find_spec(module) is None:missing.append('runtime_dependency:'+module)
        readiness.append({'provider':name,'missing_configuration':sorted(set(missing)),
                          'byte_output_adapter':'not_implemented',
                          'rerun_state':'not_executed',
                          'note':'Current benchmark adapter exposes text/links, not validated returned file bytes.'})
    meta={**dataset,'cases':[vars(c) for c in cases],
          'coverage_plan':{'file-control':['http-byte-control']},
          'scope':'Independent HTTP byte-delivery control, not one of the twelve supplier results'}
    args.output.mkdir(parents=True)
    control={'type':'http','max_bytes':20000000}
    control_path=args.output/'provider-config.json'
    control_path.write_text(json.dumps({'providers':{'http-byte-control':control}},indent=2)+'\n')
    records,_=run_evaluation(output_dir=args.output,dataset_meta=meta,
        dataset_path=ROOT/'evaluation/dataset.json',cases=cases,
        providers=[build_provider('http-byte-control',control)],
        provider_config_path=control_path,runs=1,timeout=45,delay=0)
    (args.output/'scope.json').write_text(json.dumps({'scope':meta['scope'],
        'planned_supplier_calls':len(names)*len(cases),'executed_supplier_calls':0,
        'supplier_readiness':readiness,'control_calls':len(records),
        'control_pass':sum(r['metrics'].get('raw_file_pass',False) and r['ok'] and not r['metrics'].get('deadline_overrun') for r in records)},ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
