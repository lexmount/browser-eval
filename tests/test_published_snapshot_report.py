"""A saved report matrix rebuilds through the maintained WebFetch renderer."""
import json

import pytest

from harness.record_io import canonical_json_sha256, file_sha256
from harness.web_fetch.published_snapshot import build_snapshot_audit, check_snapshot_audit, validate_snapshot_audit
from harness.web_fetch.report_explainer import _headless_article_input
from harness.web_fetch.rescoring import score_published_snapshot
from lexbench_webfetch.scoring import score_attempt


def example(root, recover_original=False):
    source=root/'source';source.mkdir()
    cases=[{'id':key,'name':key,'url':'https://example.test/'+key,'track':'web','category':'web',
            'required_facts':[{'id':'body','alternatives':['complete answer']}]} for key in ['complete','missing']]
    dataset={'id':'published-example','name':'Published example','cases':cases,
             'coverage_plan':{'web':['chromium']},'comparison':{'kind':'headless_markdown',
             'timeout_seconds':5,'runs_per_case':1,'concurrency':1,'max_failure_retries':0}}
    rows=[]
    for case in cases:
        raw=b'complete answer' if case['id']=='complete' else b'partial output'
        path=source/'artifacts'/f'{case["id"]}.txt';path.parent.mkdir(exist_ok=True);path.write_bytes(raw)
        reference=source/'references'/f'{case["id"]}.txt';reference.parent.mkdir(exist_ok=True);reference.write_text('complete answer')
        rows.append({'provider':'chromium','provider_type':'fixture','case_id':case['id'],'case_name':case['name'],
            'category':'web','attempt_id':'chromium__'+case['id']+'__r001','run':1,'run_id':'call-'+case['id'],
            'url':case['url'],'started_at':'2026-10-01T00:00:00Z','elapsed_ms':100,
            'ok':True,'status_code':200,'title':None,'final_url':case['url'],'error_type':None,
            'error_message':None,'provider_metadata':{},'artifact_path':path.relative_to(source).as_posix(),
            'metrics':score_attempt(case,raw,ok=True)})
    rows[0]['metrics']['content_pass']=False
    (source/'dataset.json').write_text(json.dumps(dataset))
    (source/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (source/'execution.json').write_text(json.dumps({'batches':[{'timeout_seconds':5,'attempt_ids':[row['attempt_id'] for row in rows]}]}))
    privacy=[];recovered={}
    if recover_original:
        artifact=source/rows[0]['artifact_path'];original=root/'recovered.txt';original.write_bytes(artifact.read_bytes())
        artifact.write_bytes(original.read_bytes()+b' [REDACTED]')
        privacy.append({'path':rows[0]['artifact_path'],'original_sha256':file_sha256(original),
                        'published_sha256':file_sha256(artifact),'published_bytes':artifact.stat().st_size,'scored_output':True})
        recovered[rows[0]['artifact_path']]=original
    (source/'publication-privacy.json').write_text(json.dumps({'files':privacy}))
    result,receipt=score_published_snapshot(source,dataset,recovered=recovered)
    manifest={'files':{p.relative_to(source).as_posix():{'sha256':file_sha256(p)} for p in source.rglob('*') if p.is_file()}}
    def save(name,value):
        path=root/name;path.write_text(json.dumps(value,ensure_ascii=False));return {'path':name,'sha256':file_sha256(path)}
    review={'title':'Saved output review','summary':'One corrected serialization decision.',
        'figure_caption':'Saved output and acceptance rules.','metric_note':'Two saved calls, no new calls.',
        'focus_heading':'Sample configuration','focus_summary':'One correction.',
        'privacy_summary':'All original bytes are present.','privacy_findings':[],
        'review_scope':'One of two results reviewed.','total_attempts':2,'reviewed_attempts':1,
        'providers':[{'provider':'chromium','published_passes':0,'historical_passes':0,'initial_rescore_passes':0,'corrected_passes':1,'total':2}],
        'changes':[{'attempt_id':result[0]['attempt_id'],'provider':'chromium','case_id':'complete',
            'historical_pass':False,'initial_rescore_pass':False,'after_fix_pass':True,
            'classification':'Serialization defect','requirement':'Return the complete answer.',
            'actual':'complete answer','reason':'The saved answer includes the required text.',
            'treatment':'Use the shared scorer.','source_scope':'Frozen fixture reference.','evidence':[],'quotes':[]}]}
    article={'schema':'webfetch_browser_interpretation/1','evidence_sha256':canonical_json_sha256(_headless_article_input({c['id']:c for c in cases},result,['chromium'])),
        'title':'Saved result interpretation','intro':'Two saved calls under the same contract.',
        'scoring':'Required text must occur in the delivered answer.','conclusions':'One answer meets its contract.',
        'providers':{'chromium':{'specimen_case_id':'missing','explanation':'The partial output lacks the required text.'}}}
    inputs={'dataset':save('corrected.json',dataset),'receipt':save('receipt.json',receipt),
            'review':save('review.json',review),'interpretation':save('interpretation.json',article),
            'snapshot_manifest':save('manifest.json',manifest),
            'historical_verdicts':save('historic.json',{'verdicts':{r['attempt_id']:False for r in rows}})}
    initial=root/'initial.jsonl';initial.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    inputs['initial_results']={'path':initial.name,'sha256':file_sha256(initial)}
    result_path=root/'corrected.jsonl';result_path.write_text(''.join(json.dumps(row)+'\n' for row in result))
    inputs['results']={'path':result_path.name,'sha256':file_sha256(result_path)}
    figure=root/'figure.png';figure.write_bytes(b'fixture image');inputs['figure']={'path':figure.name,'sha256':file_sha256(figure)}
    profile={'variant':'published-snapshot-audit','certified':False,'inputs':inputs,
        'source':{'url':'https://example.test/report','version':'frozen','archive_sha256':'0'*64},
        'correction_provenance':{'category':'format-normalization','root_cause':'Visible native label boundaries must preserve complete declared words.',
             'regression_ids':['native-serialization-stock-label','native-serialization-stock-word-fragment']}}
    path=root/'profile.json';path.write_text(json.dumps(profile));return source,path


@pytest.mark.parametrize("recover_original",[False,True])
def test_saved_matrix_keeps_native_routes_and_inspectable_review(tmp_path,recover_original):
    source,profile=example(tmp_path,recover_original=recover_original);before={p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file()}
    out=tmp_path/'report';build_snapshot_audit(source,out,profile=profile)
    assert '2 saved calls' in check_snapshot_audit(out)
    from harness.web_fetch.report_package import validate_release
    with pytest.raises(ValueError,match='require --snapshot-audit'):validate_release(out)
    assert validate_release(out,snapshot_audit=True) is False
    assert not (out/'publication-privacy.json').exists()
    assert (out/'audit/history/publication-privacy.json').is_file()
    from harness.web_fetch.report_package import build
    from harness.web_fetch.publication_check import check
    archive=tmp_path/'report.zip';build(out,archive,snapshot_audit=True)
    check(archive,out,snapshot_audit=True)
    for path in ['index.html','guide/index.html','datasets/index.html','analysis/index.html','attempts/chromium__complete__r001.html','attempts/chromium__missing__r001.html','en/index.html']:
        assert (out/path).is_file()
    assert {p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file()}==before
    from bs4 import BeautifulSoup
    for route in ['index.html','guide/index.html','analysis/index.html','audit/index.html']:
        page=BeautifulSoup((out/route).read_text(),'html.parser')
        assert page.select_one('main[data-evidence-state]') is not None
        assert not page.select('[role="status"][data-evidence-state]')
        for unwanted in ['保存输出的评分复核','本地未认证诊断结果','任务判定汇总（非排名）']:
            assert unwanted not in page.get_text()
    method=out/'methodology/index.html';original=method.read_text()
    method.write_text(original.replace('data-scoring-input-scope','data-removed-scope'))
    with pytest.raises(ValueError,match='lost its scoring input scope'):check_snapshot_audit(out)
    method.write_text(original)
    detail=out/'attempts/chromium__complete__r001.html';detail.write_text(detail.read_text().replace('data-score-audit','data-deleted-audit'))
    with pytest.raises(ValueError,match='lost its audit explanation'):check_snapshot_audit(out)


@pytest.mark.parametrize('defect',['extra-file','changed-source','changed-call','wrong-view','wrong-count','review-omission'])
def test_snapshot_boundary_rejects_unbound_evidence_and_call_changes(tmp_path,defect):
    source,profile=example(tmp_path);config=json.loads(profile.read_text())
    if defect=='extra-file':(source/'private-note.txt').write_text('undeclared')
    elif defect=='changed-source':(source/'artifacts/complete.txt').write_text('tampered')
    elif defect in {'wrong-count','review-omission'}:
        p=tmp_path/config['inputs']['review']['path'];review=json.loads(p.read_text())
        if defect=='wrong-count':review['providers'][0]['corrected_passes']=2
        else:review['changes']=[];review['reviewed_attempts']=0
        p.write_text(json.dumps(review));config['inputs']['review']['sha256']=file_sha256(p);profile.write_text(json.dumps(config))
    else:
        p=tmp_path/config['inputs']['results']['path'];rows=[json.loads(x) for x in p.read_text().splitlines()]
        if defect=='changed-call':rows[0]['elapsed_ms']=1
        else:rows[0]['scoring']['artifact_view']='published_projection'
        p.write_text(''.join(json.dumps(row)+'\n' for row in rows));config['inputs']['results']['sha256']=file_sha256(p);profile.write_text(json.dumps(config))
    with pytest.raises(ValueError):validate_snapshot_audit(source,profile)


def test_snapshot_package_uses_the_shared_publisher_without_certification(tmp_path):
    from dataclasses import replace
    from harness.web_fetch.report_package import build
    from harness.web_fetch.report_publish import PublishConfig, publish
    source,profile=example(tmp_path);out=tmp_path/'report'
    build_snapshot_audit(source,out,profile=profile);archive=tmp_path/'report.zip'
    build(out,archive,snapshot_audit=True)
    target=tmp_path/'publication.json';target.write_text(json.dumps({'artifact_hub_base_url':'https://example.test'}))
    config=PublishConfig(target,archive,out,tmp_path/'publication-receipt.json',tmp_path/'operation.json',mode='create',visibility='private')
    def transport(path,data=None,content_type=None,**kwargs):
        if path=='/api/sites':return 200,json.dumps({'slug':'new-audit','versionId':'ver_new'}).encode()
        if path=='/api/sites/new-audit/sharing':return 200,b'{"visibility":"private"}'
        raise AssertionError(path)
    with pytest.raises(ValueError,match='require --snapshot-audit'):publish(config,transport=transport)
    result=publish(replace(config,snapshot_audit=True),transport=transport)
    assert result['visibility']=='private' and result['url']=='https://example.test/s/new-audit'
