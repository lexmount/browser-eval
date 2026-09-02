"""Independently attempt every task, including resource URLs; do not use provider output."""
import sys,json,re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
import requests
from collect_source_evidence import capture
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.dynamic_lists import evaluate_dynamic_list

OUT=ROOT/'research/source-checks'
def inspect(case):
    case=dict(case,task_kind=case.get('task_kind','text'),track=case.get('track','web'))
    result=capture(case,OUT)
    p=OUT/case['id'];text=(p/'reference.txt').read_text() if (p/'reference.txt').exists() else ''
    if case['task_kind']=='structure' and (p/'source.html').exists():
        probe=evaluate_dynamic_list((p/'source.html').read_text(errors='replace'),case['source_benchmark'],case['url'],result.get('status')==200)
        result['independent_structure']={k:v for k,v in probe.items() if k not in {'dynamic_titles'}}
    assets=[]
    for group in case.get('expected_resources',[]):
        # Check every frozen alternative, without downloading large media in full.
        for url in group.get('urls',[]):
            if not url.startswith(('http://','https://')):continue
            try:
                with requests.get(url,stream=True,timeout=(10,20)) as r:
                    prefix=next(r.iter_content(512),b'')
                    assets.append({'id':group['id'],'url':url,'status':r.status_code,'final_url':r.url,'mime':r.headers.get('Content-Type'),'prefix_bytes':len(prefix)})
            except requests.RequestException as e:assets.append({'id':group['id'],'url':url,'error':str(e)[:180]})
    result['resource_checks']=assets
    result['task_kind']=case['task_kind'];result['track']=case['track']
    result['requires_browser']=result.get('status')!=200 or bool(result.get('missing_facts')) or len(text)<case.get('min_content_chars',1) or (case['task_kind']=='structure' and not result.get('independent_structure',{}).get('structure_pass'))
    (p/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result

if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    cases=json.loads((ROOT/'evaluation/dataset.json').read_text())['cases']
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(inspect,cases))
    (OUT/'audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print('ALL TASKS ATTEMPTED',len(results),flush=True)
