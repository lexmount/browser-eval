"""Select a provider-blind, reference-length-stratified ordinary-web speed cohort."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit


def select_cases(cases, count, excluded=frozenset()):
    eligible=[c for c in cases if c.get('track','web')=='web'
              and c.get('task_kind','body')=='body' and c.get('ground_truth_main_content')
              and c['id'] not in excluded]
    if count%5 or len(eligible)<count:
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--count',type=int,default=20)
    parser.add_argument('--exclude-case-ids',default='',
                        help='Provider-blind preflight reference exclusions')
    args=parser.parse_args()
    if args.output.exists():parser.error('Destination exists')
    dataset=json.loads(args.dataset.read_text())
    try:
        selected=select_cases(dataset['cases'],args.count,
                              set(filter(None,args.exclude_case_ids.split(','))))
    except ValueError as error:
        parser.error(str(error))
    result=deepcopy(dataset);result['cases']=selected
    result['comparison']={'kind':'headless_same_window_speed',
        'selection':'Provider-blind reference-length quantile sampling over ordinary web body tasks; at most one case per hostname',
        'planned_before_calls':True,'case_count':len(selected)}
    args.output.mkdir(parents=True)
    (args.output/'dataset.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for folder in ('sources','list-references'):
        source=args.dataset.parent/folder
        if source.exists():shutil.copytree(source,args.output/folder)
    print(','.join(c['id'] for c in selected))


if __name__=='__main__':main()
