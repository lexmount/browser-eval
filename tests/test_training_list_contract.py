"""Full public task acceptance for equivalent training-list representations."""
import json
from pathlib import Path

import pytest

from harness.web_fetch.metrics import task_passed
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_regressions import load_scoring_regressions
from tests.conftest import read_frozen_dataset_bytes

ROOT=Path(__file__).resolve().parents[1]
TITLE='教材《C++版信息学奥赛一本通编程启蒙》题单'


def training_case():
    return next(c for c in json.loads(read_frozen_dataset_bytes(ROOT/'evaluation/risk-control/dataset.json'))['cases']
                if c['id']=='risk-diverse-153')


def training_output(*,combined=True,rows=26,missing=None):
    lines=[TITLE,'Login to join training plan','Problem Set',
           'Home Problem Set Training Contest Record Ranking Login Sign Up Language']
    for section in range(1,6):
        lines.append(f'Section {section}. 顺序结构')
        lines.append('Problem\tTried\tAC\tDifficulty' if combined else 'Number\tName\tTried\tAC\tDifficulty')
        for i in range((section-1)*6+1,min(section*6,rows)+1):
            number=f'P{i}' if missing!='number' else ''
            name=f'练习题 {i}' if missing!='name' else ''
            values=[number+' '+name] if combined else [number,name]
            values += ['' if missing==field else '7' for field in ('attempts','accepted','difficulty')]
            lines.append('\t'.join(values))
    lines.append('Section 6. 顺序结构 - 浮点类型double')
    text='\n'.join(lines)
    return text.replace(TITLE,'') if missing=='title' else text


@pytest.mark.parametrize('combined', [True,False])
def test_complete_list_passes_public_scorer(combined):
    case=training_case()
    result=score_attempt(case,training_output(combined=combined).encode(),ok=True,final_url=case['url'])
    assert result['stable_anchor_hits']==15
    assert result['composite_group_results'][0]['count']==26
    assert task_passed(True,result,case['task_kind']) is True


@pytest.mark.parametrize('missing',['title','number','name','attempts','accepted','difficulty'])
def test_incomplete_training_list_fails_public_scorer(missing):
    case=training_case()
    result=score_attempt(case,training_output(missing=missing).encode(),ok=True,final_url=case['url'])
    assert task_passed(True,result,case['task_kind']) is False


def test_training_quantity_and_section_scope_remain_required():
    case=training_case()
    for text in [training_output(rows=20),training_output().replace('Section 1.', 'Section 10.'),
                 training_output().replace('Section 6.', 'Section 60.')]:
        assert task_passed(True,score_attempt(case,text.encode(),ok=True,final_url=case['url']),case['task_kind']) is False


def test_training_revision_preserves_the_entire_predecessor_dataset_and_profile():
    import copy
    import hashlib
    receipt=json.loads((ROOT/'research/risk-control-training-list-revision.json').read_text())
    correction=receipt['correction']
    dataset=json.loads(read_frozen_dataset_bytes(ROOT/'evaluation/risk-control/dataset.json'))
    full_page=json.loads((ROOT/'research/risk-control-full-page-rule-revision.json').read_text())
    historical=copy.deepcopy(dataset)
    for case in historical['cases']:
        count=full_page['predecessor_target_anchor_counts'][case['id']]
        anchors=case['acceptance_contract']['stable_text_anchors']
        context=[{'id':f'page-context-{i+1:02d}','alternatives':[a['alternatives'][0]]}
                 for i,a in enumerate(anchors[count:])]
        case['acceptance_contract']['stable_text_anchors']=anchors[:count]
        case['source_benchmark']['risk_control']['page_context_anchors']=context
    historical['stable_literal_count']=729
    historical['observed_literal_count']=1600
    historical={k:v for key,value in historical.items()
                for k,v in ([(key,value),('page_context_literal_count',871)]
                            if key=='stable_literal_count' else [(key,value)])}
    historical['source_bindings']=full_page['predecessor_source_bindings']
    encode=lambda x:(json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode()
    assert hashlib.sha256(encode(historical)).hexdigest()==full_page['correction']['predecessor_dataset_canonical_sha256']
    group=next(c for c in historical['cases'] if c['id']==receipt['case_id'])['composite_acceptance']['groups'][0]
    assert group['minimum_records']==21
    assert [s['id'] for s in group['required_columns']]==receipt['independent_browser_observation']['required_fields_unchanged']
    group['start_pattern']='(?:第一章|Chapter 1)';group['end_pattern']='(?:第六章|Chapter 6)';group.pop('combined_columns')
    historical['source_bindings']['list_contracts_sha256']=correction['predecessor_list_contracts_sha256']
    encode=lambda x:(json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode()
    assert hashlib.sha256(encode(historical)).hexdigest()==correction['predecessor_dataset_sha256']
    profile=json.loads((ROOT/'evaluation/risk-control/run-profile.json').read_text())
    profile['source_bindings']=full_page['predecessor_profile_source_bindings']
    profile['dataset']['sha256']=full_page['correction']['predecessor_dataset_sha256']
    assert hashlib.sha256(encode(profile)).hexdigest()==full_page['correction']['predecessor_run_profile_sha256']
    profile['dataset']['sha256']=correction['predecessor_dataset_sha256']
    profile['source_bindings']['list_contracts']['sha256']=correction['predecessor_list_contracts_sha256']
    assert hashlib.sha256(encode(profile)).hexdigest()==correction['predecessor_run_profile_sha256']
    assert correction['provider_calls']==0
    controls=load_scoring_regressions()
    assert {controls[i]['failure_mode'] for i in receipt['regression_ids']}=={'false-positive','false-negative'}
    contracts=json.loads((ROOT/'evaluation/risk-control/list-contracts.json').read_text())
    assert contracts['contracts'][receipt['case_id']]['groups']==training_case()['composite_acceptance']['groups']


def test_complete_training_list_cannot_omit_declared_navigation():
    case=training_case()
    text=training_output().replace('Home Problem Set Training Contest Record Ranking Login Sign Up Language','')
    result=score_attempt(case,text.encode(),ok=True,final_url=case['url'])
    assert result['composite_group_results'][0]['count']==26
    assert task_passed(True,result,case['task_kind']) is False
