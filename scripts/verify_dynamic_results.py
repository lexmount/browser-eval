"""Apply independent detail evidence to the fixed first-three verification contract."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from browser_harness_eval.list_verification import verify_list

def main():
    source=ROOT/'evaluation';dataset=json.loads((source/'dataset.json').read_text());cases={c['id']:c for c in dataset['cases']}
    details=json.loads((source/'list-references/details.json').read_text())
    access_details=json.loads((source/'list-references/xiaohongshu-details.json').read_text())
    details.extend(x for x in access_details if x.get('title'))
    references={}
    for c in cases.values():
        if c.get('task_kind')!='structure':continue
        path=source/'list-references'/f'{c["id"]}.json';ref=json.loads(path.read_text())
        ref['access_checks']=[x for x in access_details if x.get('restriction') and any(host in x['url'] for host in c['source_benchmark']['hosts'])]
        for item in details:
            if any(host in item['url'] for host in c['source_benchmark']['hosts']):
                if item not in ref['items']:ref['items'].append(item)
        path.write_text(json.dumps(ref,ensure_ascii=False,indent=2)+'\n');references[c['id']]=ref
    records=[json.loads(l) for l in (source/'results.jsonl').read_text().splitlines()]
    for r in records:
        if r['case_id'] not in references:continue
        c=cases[r['case_id']]
        r['metrics'].update(verify_list((source/r['artifact_path']).read_text(),c['source_benchmark'],c['url'],references[c['id']],r['ok']))
    (source/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    execution=json.loads((source/'execution.json').read_text());dynamic={r['attempt_id'] for r in records if r['case_id'] in references}
    for b in execution['batches'][:-1]:
        b['attempt_ids']=[x for x in b['attempt_ids'] if x not in dynamic]
        b['attempt_count']=len(b['attempt_ids'])
    execution['batches']=[b for b in execution['batches'] if b['attempt_ids']]
    last=execution['batches'][-1];last['attempt_count']=len(last['attempt_ids'])
    assert sum(len(b['attempt_ids']) for b in execution['batches'])==1540
    (source/'execution.json').write_text(json.dumps(execution,ensure_ascii=False,indent=2)+'\n')
    print('Verified dynamic outcomes:',sum(r['metrics'].get('list_task_pass',False) for r in records))

if __name__=='__main__':main()
