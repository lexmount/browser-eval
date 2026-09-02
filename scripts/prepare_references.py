"""Normalize scoring references and audit all facts without touching raw snapshots."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from browser_harness_eval.reference_quality import clean_reference_text,audit_reference
from browser_harness_eval.metrics import normalize_for_match


def prepare(path):
    dataset=json.loads(path.read_text());changes=[];issues=[]
    for c in dataset['cases']:
        raw=c.get('ground_truth_main_content','')
        if not raw:continue
        clean=clean_reference_text(raw)
        if clean!=raw:
            c['source_verification']['extracted_reference_sha256']=hashlib.sha256(raw.encode()).hexdigest()
            c['source_verification']['reference_sha256']=hashlib.sha256(clean.encode()).hexdigest()
            c['source_verification']['reference_normalization']='Remove U+200B, U+FEFF and discretionary U+00AD; raw capture unchanged.'
            c['ground_truth_main_content']=clean
            changes.append(c['id'])
        c['reference_answer']=clean_reference_text(c.get('reference_answer',''))
        c['expected_any']=[clean_reference_text(x) for x in c['expected_any']]
        for fact in c.get('required_facts',[]):
            fact['alternatives']=[clean_reference_text(x) for x in fact['alternatives']]
        problems=audit_reference(clean,c.get('required_facts',[]),normalize=normalize_for_match)
        if problems:issues.append({'case_id':c['id'],'issues':problems})
    if issues:raise ValueError(json.dumps(issues,ensure_ascii=False))
    path.write_text(json.dumps(dataset,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'cleaned_cases':changes,'audited_cases':sum(bool(c.get('ground_truth_main_content')) for c in dataset['cases']),'issues':issues},ensure_ascii=False))


if __name__=='__main__':prepare(ROOT/'evaluation/dataset.json')
