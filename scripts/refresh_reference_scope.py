"""Re-extract an explicitly selected source snapshot, without fetching a provider."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.storage import current_evidence_dir
from collect_source_evidence import extract_html
from harness.web_fetch.reference_quality import clean_reference_text,audit_reference
from harness.web_fetch.metrics import normalize_for_match
from harness.web_fetch.safety import contained_path,safe_identifier


def refresh(source,case_id):
    safe_identifier(case_id)
    path=contained_path(source,'dataset.json');data=json.loads(path.read_text())
    c=next(c for c in data['cases'] if c['id']==case_id)
    snapshot=contained_path(source,'sources/'+case_id+'/source.html.txt')
    raw=snapshot.read_bytes()
    title,text,selector,_=extract_html(raw,c['url'])
    if selector=='body':raise ValueError('Whole-body extraction must not automatically replace a reference')
    text=clean_reference_text(text)
    issues=audit_reference(text,c.get('required_facts',[]),normalize=normalize_for_match)
    if issues:raise ValueError(str(issues))
    c['ground_truth_main_content']=text
    c['source_verification'].update(selector=selector,method='独立源页快照中的正文结构提取',
        reference_sha256=hashlib.sha256(text.encode()).hexdigest(),source_snapshot_sha256=hashlib.sha256(raw).hexdigest(),
        reference_scope='文章正文与默认可见的常见问题标题；不含帮助中心导航，不展开折叠回答。')
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'case_id':case_id,'selector':selector,'reference_chars':len(text),'source_sha256':hashlib.sha256(raw).hexdigest()},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('case_id');args=p.parse_args()
    refresh(current_evidence_dir(),args.case_id)
