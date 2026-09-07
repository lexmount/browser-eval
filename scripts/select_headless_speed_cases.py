"""Select a provider-blind, reference-length-stratified ordinary-web speed cohort."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--count',type=int,default=20)
    args=parser.parse_args()
    if args.output.exists():parser.error('Destination exists')
    dataset=json.loads(args.dataset.read_text())
    eligible=[c for c in dataset['cases'] if c.get('track','web')=='web'
              and c.get('task_kind','body')=='body' and c.get('ground_truth_main_content')]
    if args.count%5 or len(eligible)<args.count:parser.error('Count must be a feasible multiple of five')
    eligible.sort(key=lambda c:(len(c['ground_truth_main_content']),c['id']))
    selected=[]
    for index in range(5):
        bucket=eligible[len(eligible)*index//5:len(eligible)*(index+1)//5]
        take=args.count//5
        picks=[bucket[len(bucket)*i//take] for i in range(take)]
        selected.extend(picks)
    result=deepcopy(dataset);result['cases']=selected
    result['comparison']={'kind':'headless_same_window_speed',
        'selection':'Provider-blind five-bin reference-length stratification over ordinary web body tasks',
        'planned_before_calls':True,'case_count':len(selected)}
    args.output.mkdir(parents=True)
    (args.output/'dataset.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for folder in ('sources','list-references'):
        source=args.dataset.parent/folder
        if source.exists():shutil.copytree(source,args.output/folder)
    print(','.join(c['id'] for c in selected))


if __name__=='__main__':main()
