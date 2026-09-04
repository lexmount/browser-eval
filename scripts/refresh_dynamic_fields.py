"""Auditably complete captured field contracts and rescore only dynamic attempts."""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.dynamic_reference import entry_facts
from harness.web_fetch.render_evidence import validate_render_reference
from scripts.rescore_content import rescore
from harness.web_fetch.storage import current_evidence_dir
from harness.web_fetch.storage import research_evidence_dir


def refresh(source, captures, audit_path):
    if audit_path.exists():raise ValueError('Scoring audit exists; refusing to overwrite')
    path=source/'dataset.json';dataset=json.loads(path.read_text())
    ids={c['id'] for c in captures};by={c['id']:c for c in dataset['cases']}
    rows=[json.loads(s) for s in (source/'results.jsonl').read_text().splitlines()]
    before={'cases':[json.loads(json.dumps(by[k])) for k in sorted(ids)],
            'records':[r for r in rows if r['case_id'] in ids]}
    audits={}
    for capture in captures:
        c=by[capture['id']]
        c['required_facts']=entry_facts(c['id'],capture['entries'])
        audit=validate_render_reference(c,(source/'sources'/c['id']/'initial.html.txt').read_bytes(),capture['rendered_text'])
        if not audit['valid']:raise ValueError(audit['errors'])
        audits[c['id']]=audit
    # Preserve pre-correction rules/scores and exact output hashes; no supplier call.
    audit_path.parent.mkdir(parents=True,exist_ok=True)
    payload={'reason':'Complete every observed text field; allow number/review-label whitespace without changing numeric identity',
             'before':before,'output_sha256':{r['attempt_id']:r['metrics']['content_sha256'] for r in before['records']}}
    audit_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    path.write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+'\n')
    for key,audit in audits.items():(source/'sources'/key/'render-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    changes=rescore(source,case_ids=ids)
    after=[json.loads(s) for s in (source/'results.jsonl').read_text().splitlines()]
    assert [r for r in rows if r['case_id'] not in ids]==[r for r in after if r['case_id'] not in ids]
    payload['pass_changes']=changes
    payload['after_rules_sha256']=hashlib.sha256(json.dumps([by[k] for k in sorted(ids)],sort_keys=True).encode()).hexdigest()
    audit_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(f'Rescored {len(before["records"])} preserved attempts; pass changes: {changes}')


if __name__=='__main__':
    refresh(current_evidence_dir(),json.loads((ROOT/'research/dynamic-browser-captures.json').read_text()),research_evidence_dir()/'dynamic-score-audit.json')
