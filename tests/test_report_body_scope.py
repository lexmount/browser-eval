import copy
import hashlib
import html
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.report import (
    TRACKS, attempt_row, case_rate, generate, overview_card, ranking, write_support,
)
from harness.web_fetch.report_components import (
    task_contract_guide, task_pass_guide, examples, list_rule_description, metric_guide, reference_status, task_instruction,
)


def case(identifier='list', kind='structure', track='challenge'):
    return dict(id=identifier, name=identifier, category='test', track=track,
                task_kind=kind, url='https://example.com/'+identifier,
                content_score_applicable=kind=='body',
                ground_truth_main_content='reference' if kind=='body' else '',
                source_benchmark={'verification_scope':'first_three_name_detail_pairs'} if kind=='structure' else {}, required_facts=[])


def record(c, passed=True, ok=True, score=.8, elapsed=100):
    return dict(provider='fixture', provider_type='fixture', case_id=c['id'],
                case_name=c['name'], category=c['category'], url=c['url'], run=1,
                attempt_id='fixture__'+c['id']+'__r001', started_at='2026-09-03T00:00:00Z',
                elapsed_ms=elapsed, ok=ok, error_type=None, status_code=200,
                artifact_path='artifacts/fixture/'+c['id']+'/run-001.txt',
                metrics=dict(content_pass=passed, list_task_pass=passed,
                             word_f1=score, word_recall=score, word_precision=score,
                             length_pass=True, fact_results=[], pattern_results=[],
                             resource_results=[], confirmed_entries=3,
                             content_sha256=hashlib.sha256(b'output').hexdigest()))


def table_cells(rendered, identifier):
    table = re.search(r'<table\b[^>]*\bid="'+re.escape(identifier)+r'"[^>]*>(.*?)</table>', rendered, re.S)[1]
    body = table.split('<tbody', 1)[1]
    return [[html.unescape(re.sub('<[^>]+>', '', cell)) for cell in re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)]
            for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', body, re.S)]


class ReportBodyScopeTests(unittest.TestCase):
    def test_text_coverage_repeated_denominator_removed_but_different_scopes_disclosed(self):
        a,b=case('a','body'),case('b','body')
        first,second=record(a),record(b)
        second['provider']='other'
        for r in [first,second]:r['metrics']['fact_results']=[{'id':'x','pass':True}]
        rendered=ranking([first,second],{'a':a,'b':b},'challenge')
        self.assertIn('各方法文字核对范围不同',rendered)
        self.assertIn('<summary>文字核对覆盖范围</summary>',rendered)
        for row in table_cells(rendered,'ranking-challenge'):
            self.assertEqual(row[7],'100.0%')
        self.assertNotIn('· 1/2 例',rendered)

    def test_four_text_tracks_share_columns_and_text_coverage_semantics(self):
        headers=[]
        for track in ('web','dynamic','challenge','document'):
            c=case('example','body');c['track']=track
            r=record(c,passed=False)
            r['metrics']['fact_results']=[{'id':'a','pass':True},{'id':'b','pass':False}]
            rendered=ranking([r],{'example':c},track)
            row=table_cells(rendered,'ranking-'+track)[0]
            self.assertEqual(row[7],'50.0%')
            self.assertIn('文字核对范围：各方法均为 1/1 例',rendered)
            self.assertEqual(row[4:7],['80.0%']*3)
            self.assertEqual(row[3],'0.0%')
            headers.append([re.sub(r'<[^>]*>','',h) for h in re.findall(r'<th[^>]*>(.*?)</th>',rendered,re.S)])
        self.assertTrue(all(h==headers[0] for h in headers))

    def test_nonliteral_snapshot_not_promoted_into_invariant_body_quality(self):
        c=case('example','body'); c['track']='dynamic'
        c['acceptance_contract']={'schema':'product','minimum_records':3}
        c['source_benchmark']={'stable_annotation':{'structural_only_fields':['price']}}
        r=record(c)
        row=table_cells(ranking([r],{'example':c},'dynamic'),'ranking-dynamic')[0]
        self.assertEqual(row[4:7],['—']*3)
        self.assertEqual(r['metrics']['word_f1'],.8)

    def test_current_instruction_does_not_reuse_old_identity_question(self):
        c=case()
        c['task_prompt']='核对返回的前三条名称与详情身份'
        c['acceptance_contract']={'schema':'named_link','minimum_records':3,'stable_text_anchors':[]}
        rendered=task_instruction(c)
        self.assertIn('至少 3 条',rendered)
        self.assertIn('标题、详情链接',rendered)
        self.assertNotIn('前三条名称与详情身份',rendered)
        from harness.web_fetch.report import reason, stable_checks
        r={'ok':True,'metrics':{'stable_task_pass':True}}
        self.assertIn('未设置稳定文字核对点',reason(r,c))
        self.assertNotIn('稳定内容与结构检查通过',reason(r,c))
        self.assertIn('不作文字内容已核验的结论',stable_checks(r['metrics']))

    def test_list_outcomes_and_times_are_main_metrics_without_fake_word_scores(self):
        cases = {str(i):case(str(i)) for i in range(10)}
        records = [record(c) for c in cases.values()]
        before = copy.deepcopy((cases, records))
        rendered = ranking(records, cases, 'challenge')
        self.assertIn('完整参考覆盖 0/10', rendered)
        row = table_cells(rendered, 'ranking-challenge')[0]
        self.assertEqual(row[2], '100.0%')  # Transport remains measured.
        self.assertEqual(row[3], '100.0%')
        self.assertEqual(row[4:7], ['—'] * 3)
        self.assertEqual(row[8:11], ['100 ms'] * 3)
        self.assertEqual(row[11], '10/10')
        self.assertNotIn('auxiliary',rendered)
        self.assertNotIn('正文未评测',rendered)
        self.assertIn('名称—链接身份核验',rendered)
        self.assertEqual((cases, records), before)

    def test_future_body_case_in_challenge_is_measured(self):
        body, auxiliary = case('body', 'body'), case('list')
        rendered = ranking([record(body, elapsed=20), record(auxiliary, elapsed=900)],
                           {'body':body, 'list':auxiliary}, 'challenge')
        row = table_cells(rendered, 'ranking-challenge')[0]
        self.assertEqual(row[3:7], ['100.0%', '80.0%', '80.0%', '80.0%'])
        self.assertEqual(row[8], '460 ms')
        self.assertIn('完整参考覆盖 1/2', rendered)
        self.assertNotIn('ranking-challenge-auxiliary',rendered)

    def test_zero_pass_can_have_nonzero_f1_and_no_pass_latency(self):
        c=case('body','body','web')
        row=table_cells(ranking([record(c,passed=False)], {'body':c}, 'web'),'ranking-web')[0]
        self.assertEqual(row[3:7], ['0.0%', '80.0%', '80.0%', '80.0%'])
        self.assertEqual(row[8:11], ['—']*3)
        self.assertEqual(row[11], '1/1')

    def test_request_failure_zero_and_macro_case_average(self):
        first,second=case('a','body'),case('b','body')
        rs=[record(first,score=1),record(first,ok=False,score=1),record(second,score=.2)]
        row=table_cells(ranking(rs, {'a':first,'b':second}, 'challenge'),'ranking-challenge')[0]
        self.assertEqual(row[6], '35.0%')  # mean(mean(1,0), .2), not pooled .4

    def test_optional_full_reference_diagnostics_independent_of_task_kind(self):
        a,b=case('a'),case('b','body')
        a['ground_truth_main_content']='complete scoped text reference'
        b['content_score_applicable']=False
        rendered=ranking([record(a),record(b)],{'a':a,'b':b},'challenge')
        self.assertIn('完整参考覆盖 2/2',rendered)
        self.assertIn('2 例完整参考',reference_status([a,b]))
        self.assertEqual(table_cells(rendered,'ranking-challenge')[0][4:7],['80.0%']*3)

    def test_home_card_uses_genuine_task_result(self):
        c=case(); rendered=overview_card([record(c)],{'list':c},'challenge',TRACKS['challenge'],['fixture'])
        self.assertNotIn('正文未评测',rendered)
        self.assertIn('最高任务通过率 <b>100.0%</b>',rendered)
        self.assertIn('fixture',rendered)
        c=case('body','body')
        self.assertIn('100.0%',overview_card([record(c)],{'body':c},'challenge',TRACKS['challenge'],['fixture']))

    def test_case_and_attempt_labels_preserve_measured_task(self):
        c=case();r=record(c)
        self.assertEqual('100.0%',case_rate([r],c))
        attrs,rendered=attempt_row(r,c)
        self.assertIn('data-content="na"',attrs)
        self.assertIn('列表任务：通过',rendered)
        self.assertNotIn('未评测',rendered)

    def test_example_order_and_metric_explanations(self):
        rendered=examples()
        positions=[rendered.index('data-tab="'+t+'"') for t in ['web','dynamic','challenge','document','image','video']]
        self.assertEqual(positions,sorted(positions))
        guide=metric_guide()
        for phrase in ['0 次通过不等于 F1 为 0','请求失败计 0','仍按实际正文评分','不是平均完整度与平均纯净度的调和平均']:
            self.assertIn(phrase,guide)

    def test_stable_contract_and_snapshot_diagnostics_are_distinct(self):
        rendered=task_contract_guide()
        for phrase in ['数量、必需字段、唯一性和字段类型','事实准确性','实时性','完整参考',
                       '任务通过率和耗时仍保留','原始输出哈希','已验证的 stable_contract','exact_snapshot']:
            self.assertIn(phrase,rendered)
        self.assertNotIn('30 分钟',rendered)

    def test_identity_results_not_relabelled_as_mechanical_counts(self):
        c=case();r=record(c,passed=False)
        r['metrics']['structure_pass']=True
        rendered=ranking([r],{'list':c},'challenge')
        self.assertEqual(table_cells(rendered,'ranking-challenge')[0][3],'0.0%')
        self.assertIn('独立内容证据',list_rule_description(c))
        c['source_benchmark']={'minimum_entries':5}
        self.assertIn('字段／数量规则',list_rule_description(c))
        self.assertIn('至少 5',list_rule_description(c))
        self.assertNotIn('独立内容证据',list_rule_description(c))

    def test_stable_content_anchors_remain_part_of_task_acceptance(self):
        for rendered in [metric_guide(), task_contract_guide(), examples(),
                         task_pass_guide('dynamic'), task_pass_guide('challenge'),
                         reference_status([])]:
            with self.subTest(rendered=rendered[:80]):
                self.assertIn('稳定标题、栏目、说明', rendered)
                self.assertIn('频繁变化', rendered)
                self.assertIn('类型', rendered)
        for rendered in [metric_guide(), task_contract_guide()]:
            self.assertIn('不能用条目数量推算全文 F1', rendered)
            self.assertIn('可比性', rendered)
        c = case()
        c['source_benchmark'] = {'minimum_entries': 5}
        self.assertIn('稳定标题、栏目、说明', list_rule_description(c))

    def test_rendered_pages_preserve_results_and_label_only_missing_word_metrics(self):
        c=case();r=record(c)
        r['metrics'].update(effective_entries=3,minimum_entries=3)
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True);artifact.write_text('output')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[])
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['任务结果','列表任务：通过','— / — / —','全文指标无完整参考','列表任务规则 / 参考依据','有效条目数 / 声明最少数量','3 / 3']:
                self.assertIn(phrase,detail)
            self.assertNotIn('正文未评测',detail)
            self.assertNotIn('评分方式',detail)
            self.assertIn('100.0%',(out/'datasets/index.html').read_text())
            self.assertNotIn('正文未评测',(out/'datasets/list.html').read_text())
            for page in ['index.html', 'datasets/list.html']:
                self.assertIn('稳定标题、栏目、说明', (out/page).read_text())
            with patch('harness.web_fetch.report_methods.render_methods',return_value='methods'):
                write_support(out,[r],[])
            self.assertIn('task-contracts',(out/'methodology/index.html').read_text())

    def test_current_canonical_challenge_has_zero_body_coverage(self):
        from harness.web_fetch.storage import current_evidence_dir
        root=Path(__file__).resolve().parents[1]
        evidence=current_evidence_dir()
        if not (evidence/'results.jsonl').is_file():self.skipTest('requires persisted benchmark evidence')
        cases={c['id']:c for c in json.loads((evidence/'dataset.json').read_text())['cases']}
        records=[json.loads(s) for s in (evidence/'results.jsonl').read_text().splitlines()]
        if len({r['provider'] for r in records}) != 12:
            self.skipTest('requires persisted 12-provider general benchmark evidence')
        rendered=ranking(records,cases,'challenge')
        self.assertIn('完整参考覆盖 0/10',rendered)
        rows=table_cells(rendered,'ranking-challenge')
        self.assertEqual(len(rows),12)
        self.assertTrue(all(row[4:7]==['—']*3 for row in rows))
        self.assertTrue(all(row[3]!='—' for row in rows))
        from harness.web_fetch.report import succeeded
        for row in rows:
            subset=[r for r in records if r['provider']==row[1] and cases[r['case_id']].get('track')=='challenge']
            passed=[r for r in subset if succeeded(r,cases[r['case_id']])]
            self.assertEqual(row[3],f'{len(passed)/len(subset)*100:.1f}%')
            self.assertEqual(row[8]=='—',not passed)

    def test_stable_results_override_legacy_checks_in_details_and_ranking(self):
        from harness.web_fetch.report import reason
        from harness.web_fetch.report_build import scoring_scope
        c=case()
        c['acceptance_contract']=dict(version=1,schema='named_link',minimum_records=3,
            hosts=['example.com'],entry_path='/items/.*',
            page_patterns=['Catalog'],stable_text_anchors=[dict(id='heading',alternatives=['Catalog'])])
        r=record(c,passed=False)
        r['metrics']['acceptance_contract_sha256']=hashlib.sha256(json.dumps(c['acceptance_contract'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        r['scoring']=dict(mode='offline_rescore',scored_at='2026-09-04T01:00:00Z',rule_sha256='rule-hash',previous_task_pass=False)
        r['diagnosis']=dict(evidence='obsolete identity failure',category='obsolete',stage='task',certainty='old')
        r['metrics'].update(stable_task_pass=True,stable_structure_pass=True,stable_content_pass=True,
            stable_task_schema='named_link',stable_record_count=3,stable_complete_record_count=3,
            stable_unique_record_count=3,stable_minimum_records=3,stable_task_errors=[],
            stable_anchor_results=[dict(id='heading',alternatives=['Catalog'],matched=['Catalog'],pass_=True)],
            stable_page_pattern_results=[{'pattern':'Catalog','pass':True}],
            stable_records=[dict(fields={'title':'<item>','url':'https://example.com/1'},valid=True,errors=[])])
        r['metrics']['stable_anchor_results'][0]['pass']=r['metrics']['stable_anchor_results'][0].pop('pass_')
        self.assertEqual(scoring_scope([r],{'list':c}),'stable_contract')
        self.assertIn('结构检查通过',reason(r,c))
        self.assertEqual(table_cells(ranking([r],{'list':c},'challenge'),'ranking-challenge')[0][3],'100.0%')
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True);artifact.write_text('output')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[])
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['稳定内容检查','条目结构检查','完整且唯一条目数','稳定内容核对点','heading','Catalog','&lt;item&gt;']:
                self.assertIn(phrase,detail)
            self.assertNotIn('obsolete identity failure',detail)
            self.assertNotIn('条已确认',detail)
            for phrase in ['评分方式','保存输出离线评分','评分时间','2026-09-04T01:00:00Z','规则校验值','rule-hash','2026-09-03T00:00:00Z']:
                self.assertIn(phrase,detail)
            self.assertIn('稳定文本锚点',(out/'datasets/list.html').read_text())
        r['metrics'].update(stable_task_pass=False,stable_content_pass=False,
                            stable_task_errors=['missing_stable_anchor:heading'],list_task_pass=True)
        self.assertEqual(table_cells(ranking([r],{'list':c},'challenge'),'ranking-challenge')[0][3],'0.0%')
        self.assertIn('missing_stable_anchor:heading',reason(r,c))

    def test_stable_failure_categories_preserve_diagnosis_and_filters(self):
        from harness.web_fetch.report import failure_category, failure_filters
        from harness.web_fetch.failure_diagnosis import diagnose
        c=case();c['acceptance_contract']={'version':1,'schema':'product','minimum_records':3}
        for error,label in [('missing_stable_anchor:heading','缺少关键信息'),('duplicate_records','条目重复'),
                            ('incomplete_records','字段格式或记录不完整'),('too_few_complete_records','有效条目不足')]:
            with self.subTest(error=error):
                r=record(c,passed=False)
                r['metrics'].update(stable_task_pass=False,stable_task_errors=[error],stable_minimum_records=3)
                self.assertEqual(failure_category(r,c),label)
                r['diagnosis']=diagnose(r,c,'')
                self.assertEqual(failure_category(r,c),label)
                self.assertIn((label,label),failure_filters([r],{'list':c})[1][2])
                self.assertIn('data-failure-category="'+label+'"',attempt_row(r,c)[0])

    def test_schema_fields_are_explained_without_internal_version_or_empty_sections(self):
        from harness.web_fetch.report_components import stable_contract_details, FIELD_REQUIREMENTS
        from harness.web_fetch.stable_tasks import SCHEMA_FIELDS, UNIQUE_FIELDS
        for schema,fields in SCHEMA_FIELDS.items():
            c=case();c['acceptance_contract']={'version':1,'schema':schema,'minimum_records':3}
            if schema=='named_link':c['acceptance_contract'].update(hosts=['example.com'],entry_path='/items/.*')
            rendered=stable_contract_details(c)
            with self.subTest(schema=schema):
                self.assertIn('必需字段与要求',rendered)
                for field in fields:self.assertIn(FIELD_REQUIREMENTS[field][0],rendered)
                self.assertIn('＋'.join(FIELD_REQUIREMENTS[f][0] for f in UNIQUE_FIELDS[schema]),rendered)
                self.assertNotIn('v1',rendered)
                self.assertNotIn('未登记',rendered)
                self.assertNotIn('<h3>页面模式',rendered)

    def test_public_methodology_does_not_describe_legacy_identity_migration(self):
        for rendered in [metric_guide(),task_contract_guide(),examples(),reference_status([]),
                         task_pass_guide('dynamic'),task_pass_guide('challenge')]:
            for phrase in ['若登记','仍采用快照','名称—链接身份','另按其实际规则']:
                self.assertNotIn(phrase,rendered)

    def test_methodology_assets_copied_and_shown_only_when_present(self):
        from harness.web_fetch.report_build import copy_methodology_assets
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);assets=root/'input';assets.mkdir();out=root/'report'
            for name in ['annotation','validation','scoring']:
                (assets/f'{name}.png').write_bytes(b'fixture image bytes')
            copy_methodology_assets(out,assets)
            for name in ['annotation','validation','scoring']:
                self.assertEqual((out/f'assets/methodology/{name}.png').read_bytes(),b'fixture image bytes')
            with patch('harness.web_fetch.report_methods.render_methods',return_value='methods'):
                write_support(out,[],[])
            page=(out/'methodology/index.html').read_text()
            for name in ['annotation','validation','scoring']:
                self.assertIn(f'../assets/methodology/{name}.png',page)

    def test_real_methodology_pngs_available_before_generate(self):
        from harness.web_fetch.report_build import build_report
        assets=Path(__file__).resolve().parents[1]/'harness/web_fetch/assets/methodology'
        c=case();r=record(c)
        c['acceptance_contract']={'version':1,'schema':'product','minimum_records':1,'stable_text_anchors':[{'id':'title','alternatives':['商品']} ]}
        r['metrics']['acceptance_contract_sha256']=hashlib.sha256(json.dumps(c['acceptance_contract'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        r['metrics'].update(stable_task_pass=True)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';source.mkdir();out=Path(directory)/'report'
            (source/'dataset.json').write_text(json.dumps({'cases':[c]}))
            (source/'results.jsonl').write_text(json.dumps(r)+'\n')
            (source/'manifest.json').write_text('{}')
            artifact=source/r['artifact_path'];artifact.parent.mkdir(parents=True);artifact.write_bytes(b'output')
            def inspect_generate(staging,*args):
                for name in ['annotation','validation','scoring']:
                    expected=(assets/f'{name}.png').read_bytes()
                    self.assertTrue(expected.startswith(b'\x89PNG\r\n\x1a\n'))
                    self.assertEqual((staging/f'assets/methodology/{name}.png').read_bytes(),expected)
                summary=json.loads((staging/'summary.json').read_text())
                self.assertEqual(summary['challenge']['fixture']['scoring_scope'],'stable_contract')
                self.assertEqual(summary['challenge']['fixture']['task_pass'],1)
            with patch('harness.web_fetch.report_build.require_verified_reference_checks'), \
                 patch('harness.web_fetch.report_build.diagnose',return_value=None), \
                 patch('harness.web_fetch.report_build.generate',side_effect=inspect_generate) as generated:
                build_report(source,out)
            generated.assert_called_once()

    def test_build_rejects_missing_or_stale_contract_hash_before_render(self):
        from harness.web_fetch.report_build import build_report
        c=case();c['acceptance_contract']={'version':1,'schema':'product','minimum_records':3}
        r=record(c)
        for actual in [None,'bad-hash',hashlib.sha256(json.dumps({**c['acceptance_contract'],'minimum_records':2},sort_keys=True,ensure_ascii=False).encode()).hexdigest()]:
            with self.subTest(hash=actual), tempfile.TemporaryDirectory() as directory:
                source=Path(directory)/'source';source.mkdir();out=Path(directory)/'report'
                r['metrics']['acceptance_contract_sha256']=actual
                (source/'dataset.json').write_text(json.dumps({'cases':[c]}))
                (source/'results.jsonl').write_text(json.dumps(r)+'\n')
                with patch('harness.web_fetch.report_build.generate') as generated:
                    with self.assertRaisesRegex(ValueError,'Acceptance contract hash missing or mismatched'):
                        build_report(source,out)
                generated.assert_not_called()
                self.assertFalse(out.exists())


if __name__=='__main__':
    unittest.main()
