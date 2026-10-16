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
    TRACKS, _body_region_evidence, _body_scoring_text, body_region_rows, overview_all_call_latency, attempt_row, case_rate,
    composite_contract_details, composite_result_evidence, failure_category,
    generate, overview_card, ranking, reason, run_log_section,
    stable_anchor_evidence, stable_answer_panel, stable_checks,
    write_support,
)
from harness.web_fetch.report_components import (
    task_contract_guide, task_pass_guide, examples, list_rule_description, metric_guide, reference_status,
    stable_contract_details, task_instruction,
)
from lexbench_webfetch.stable_tasks import evaluate_stable_task
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
from harness.web_fetch.report_i18n import translate_text


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
                             scoring_source_sha256=SCORING_SOURCE_SHA256,
                             content_sha256=hashlib.sha256(b'output').hexdigest()))


def table_cells(rendered, identifier):
    table = re.search(r'<table\b[^>]*\bid="'+re.escape(identifier)+r'"[^>]*>(.*?)</table>', rendered, re.S)[1]
    body = table.split('<tbody', 1)[1]
    return [[html.unescape(re.sub('<[^>]+>', '', cell)) for cell in re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)]
            for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', body, re.S)]


class ReportBodyScopeTests(unittest.TestCase):
    def test_scoped_presentation_refresh_updates_only_explicit_result_details(self):
        cases=[case('first'),case('second')]
        rows=[record(c,passed=False,ok=False) for c in cases]
        for row in rows:
            row['error_type']='tool_error'
            row['error_message']='raw download output only supports automatic output or --dump json'
            row['diagnosis']={'category':'旧分类','stage':'旧阶段','evidence':'旧解释','certainty':'直接返回依据'}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            for row in rows:
                artifact=out/row['artifact_path'];artifact.parent.mkdir(parents=True,exist_ok=True);artifact.write_text('output')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':cases},rows,[])
                detail_paths=[out/'attempts'/f"{row['attempt_id']}.html" for row in rows]
                before=[path.read_bytes() for path in detail_paths]
                dataset_before=(out/'datasets/first.html').read_bytes()
                from harness.web_fetch.failure_diagnosis import diagnose
                for row in rows:row['diagnosis']=diagnose(row,cases[0],'')
                generate(out,{'cases':cases},rows,[],presentation_only=True)
                self.assertEqual([path.read_bytes() for path in detail_paths],before)
                generate(out,{'cases':cases},rows,[],presentation_only=True,detail_attempt_ids={rows[0]['attempt_id']})
                self.assertIn('不支持所需输出格式',detail_paths[0].read_text())
                self.assertNotEqual(detail_paths[0].read_bytes(),before[0])
                self.assertEqual(detail_paths[1].read_bytes(),before[1])
                self.assertEqual((out/'datasets/first.html').read_bytes(),dataset_before)
                with self.assertRaisesRegex(ValueError,'Unknown detail'):
                    generate(out,{'cases':cases},rows,[],presentation_only=True,detail_attempt_ids={'absent'})

    def test_media_report_families_follow_the_deliverable(self):
        from harness.web_fetch.models import REPORT_FAMILIES, report_family
        cases = [
            {'track': 'image', 'task_kind': 'resource'},
            {'track': 'video', 'task_kind': 'resource'},
            {'track': 'video', 'task_kind': 'body'},
            {'track': 'image', 'task_kind': 'file'},
            {'track': 'video', 'task_kind': 'file'},
        ]
        self.assertEqual(
            [report_family(case) for case in cases],
            ['web_media', 'web_media', 'web_media', 'media_file', 'media_file'],
        )
        self.assertEqual(REPORT_FAMILIES['web_media'], '网页内嵌媒体')
        self.assertEqual(REPORT_FAMILIES['media_file'], '直接媒体文件')

    def test_task_family_names_and_order_share_the_report_registry(self):
        from harness.web_fetch.report_explainer import TRACK_NAMES
        self.assertIs(TRACK_NAMES, TRACKS)
        self.assertEqual(list(TRACKS), ['web','dynamic','challenge','risk_control','document','image','video'])
        self.assertEqual(TRACKS['risk_control'], '风控网页正文')

    def test_all_call_latency_keeps_failed_calls_and_rejects_partial_matrix(self):
        c=case('body',kind='body')
        rows=[record(c,passed=True,elapsed=100),record(c,passed=False,elapsed=900)]
        rows[1]['provider']='second'
        rendered=overview_all_call_latency(rows,{c['id']:c})
        self.assertIn('900 ms',rendered)
        self.assertIn('全部调用 P95',rendered)
        with self.assertRaises(ValueError):
            overview_all_call_latency(rows,{c['id']:c,'missing':case('missing',kind='body')})

    def test_body_display_uses_visible_body_without_title_or_scripts(self):
        from lexbench_webfetch.scoring import score_attempt
        contract={'version':3,'unit':'region_compact_hexagram_recall',
                  'minimum_coverage':.8,'minimum_floor':.6,'minimum_region_passes':1,
                  'regions':[{'id':'body','text':'abcdefghijkl'}]}
        c=case('body',kind='body');c['body_requirements']=contract
        for output in ('abcdef', '<main>abcdef<script>ghijkl</script></main>'):
            with self.subTest(output=output):
                r={'ok':True,'status_code':200,'title':'ghijkl'}
                metrics=score_attempt(c,output.encode(),ok=True,title=r['title'])
                scoring_text=_body_scoring_text(r,output)
                self.assertEqual(scoring_text.strip(),'abcdef')
                rows,cards=body_region_rows(c,metrics,scoring_text)
                self.assertEqual(len(rows),1)
                self.assertNotIn('ghijkl</pre>',cards)

    def test_bound_body_evidence_is_displayed_without_rescoring(self):
        from lexbench_webfetch.scoring import score_attempt
        contract={'version':3,'unit':'region_compact_hexagram_recall',
                  'minimum_coverage':.8,'minimum_floor':.6,'minimum_region_passes':1,
                  'regions':[{'id':'body','text':'abcdefghijkl'}]}
        c=case('body',kind='body');c['body_requirements']=contract
        metrics=score_attempt(c,b'abcdefghijkl',ok=True)
        saved=copy.deepcopy(metrics)
        with patch('lexbench_webfetch.body_coverage.evaluate_body_requirements',
                   side_effect=AssertionError('presentation must not rescore saved passages')):
            rows,cards=body_region_rows(c,metrics,'abcdefghijkl')
        self.assertEqual(metrics,saved)
        self.assertEqual(len(rows),1)
        self.assertIn('abcdefghijkl',''.join(cards))

    def test_body_evidence_uses_the_canonical_global_allocation(self):
        shared='abcdefghijklmnopqrstuvwx'*3
        contract={'version':3,'unit':'region_compact_hexagram_recall',
                  'minimum_coverage':.8,'minimum_floor':.6,'minimum_region_passes':1,
                  'regions':[{'id':'opening','text':shared+'AAAAAAAABBBBBBBB'},
                             {'id':'ending','text':shared+'CCCCCCCCDDDDDDDD'}]}
        content=shared+'\n'+shared[:30]+'AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDD'
        evidence,counts=_body_region_evidence(content,contract)
        from lexbench_webfetch.body_coverage import evaluate_body_requirements
        scored={row['id']:row['matched_units'] for row in
                evaluate_body_requirements(content,contract)['body_region_results']}
        self.assertEqual(counts,scored)
        self.assertTrue(evidence['opening'])
        self.assertTrue(evidence['ending'])

    def test_body_evidence_keeps_both_lines_for_cross_line_scoring_units(self):
        contract={'version':3,'unit':'region_compact_hexagram_recall',
                  'minimum_coverage':.8,'minimum_floor':.6,'minimum_region_passes':1,
                  'regions':[{'id':'body','text':'abcdefghijkl'}]}
        evidence,counts=_body_region_evidence('abcdef\nghijkl',contract)
        self.assertEqual(evidence['body'],['abcdef','ghijkl'])
        self.assertEqual(counts['body'],7)

    def test_body_evidence_maps_one_scoring_unit_across_three_short_lines(self):
        contract={'version':3,'unit':'region_compact_hexagram_recall',
                  'minimum_coverage':.8,'minimum_floor':.6,'minimum_region_passes':1,
                  'regions':[{'id':'body','text':'abcdef'}]}
        evidence,counts=_body_region_evidence('ab\ncd\nef',contract)
        self.assertEqual(evidence['body'],['ab','cd','ef'])
        self.assertEqual(counts['body'],1)

    def test_long_stable_text_and_record_lists_scroll_only_after_five_items(self):
        anchors=[{'id':f'a-{index}','alternatives':[f'Stable text {index}']} for index in range(6)]
        contract={'minimum_records':6,'maximum_records':6,'stable_text_anchors':anchors}
        answer=stable_answer_panel(contract)
        self.assertIn('<div class="answer-list" data-scroll-after="5">',answer)
        metrics={'stable_anchor_results':[
            {'id':item['id'],'alternatives':item['alternatives'],'matched':item['alternatives'],'pass':True}
            for item in anchors],
            'stable_records':[{'fields':{'title':f'Item {index}'},'valid':True,'errors':[]} for index in range(6)],
            'stable_page_pattern_results':[],'stable_task_errors':[]}
        evidence=stable_anchor_evidence(metrics,'\n'.join(item['alternatives'][0] for item in anchors))
        self.assertIn('<div class="anchor-grid" data-scroll-after="5">',evidence)
        self.assertIn('<div class="record-scroll" data-scroll-after="5">',stable_checks(metrics))
        self.assertNotIn('data-scroll-after="5"',stable_answer_panel({**contract,'stable_text_anchors':anchors[:5]}))

    def test_diagnostic_stable_text_is_not_described_as_required(self):
        anchors=[{'id':f'a-{index}','alternatives':[f'Stable text {index}']} for index in range(6)]
        contract={'version':1,'schema':'body_text','minimum_records':0,
                  'stable_text_role':'diagnostic','stable_text_anchors':anchors}
        c=case('diagnostic-anchor','body','risk_control');c['acceptance_contract']=contract
        answer=stable_answer_panel(contract)
        details=stable_contract_details(c)
        metrics={'stable_anchors_required':False,'stable_anchor_results':[
            {'id':item['id'],'alternatives':item['alternatives'],'matched':[],'pass':False}
            for item in anchors]}
        evidence=stable_anchor_evidence(metrics,'complete body without shell labels')
        guide=task_pass_guide('risk_control',cases={c['id']:c})
        for rendered in (answer,details,evidence,guide):
            self.assertIn('不影响是否通过',rendered)
            self.assertNotIn('全部参与评分',rendered)
            self.assertNotIn('所有组均须通过',rendered)
        self.assertNotIn('必须出现的稳定文字',answer)
        self.assertIn('范围核对文字',evidence)
        self.assertNotIn('<b>标准答案</b>',evidence)

    def test_required_stable_text_keeps_acceptance_language(self):
        contract={'version':1,'schema':'body_text','minimum_records':0,
                  'stable_text_role':'required',
                  'stable_text_anchors':[{'id':'a','alternatives':['Required text']}]}
        c=case('required-anchor','body','risk_control');c['acceptance_contract']=contract
        self.assertIn('必须出现的稳定文字',stable_answer_panel(contract))
        self.assertIn('所有组均须通过',stable_contract_details(c))

    def test_diagnostic_stable_text_explanation_has_english_copy(self):
        chinese=('页面正文按登记的正文规则核验；稳定文字只用于核对抓取范围，不影响是否通过；'
                 '新闻、日期、轮播卡片、仓库状态等易变内容不固定为答案。'
                 '历史完整快照只作诊断，不进入全文质量汇总。')
        english=translate_text(chinese)
        self.assertNotEqual(english,chinese)
        self.assertIn('does not affect acceptance',english)

    def test_method_guide_distinguishes_required_and_diagnostic_anchors(self):
        rendered=task_contract_guide()
        self.assertIn('必需锚点参与验收',rendered)
        self.assertIn('诊断锚点只核对抓取范围',rendered)
        self.assertNotIn('稳定内容锚点仍用于任务验收',rendered)
        english=translate_text('标为必需的稳定内容锚点参与验收；诊断锚点只核对抓取范围。')
        self.assertIn('required affect acceptance',english)
    def test_local_report_links_complete_private_debug_log(self):
        c = case('debug-log', 'body', 'risk_control')
        r = record(c)
        r['provider_metadata'] = {
            'local_report_complete_diagnostic_links': True,
            'diagnostic_log_path': (
                'private_artifacts/moli-1.1.11/debug-log/run-001.stderr.log'
            ),
            'diagnostic_log_bytes': 1234,
            'diagnostic_log_sha256': 'a' * 64,
            'diagnostic_log_truncated': False,
        }
        with tempfile.TemporaryDirectory() as directory:
            rendered = run_log_section(Path(directory), r, 'output')
        self.assertIn('完整原始 debug 日志', rendered)
        self.assertIn(
            '../../private_artifacts/moli-1.1.11/debug-log/run-001.stderr.log',
            rendered,
        )
        self.assertIn('1,234', rendered)
        self.assertIn('截断：否', rendered)

    def test_composite_rules_and_failures_are_visible_in_report(self):
        contract = {"version": 1, "groups": [{
            "id": "articles", "kind": "named_link", "minimum_records": 8,
            "hosts": ["example.test"], "entry_path": r"/article/\d+",
            "required_fields": [{"id": "date", "kind": "date_or_time"}],
        }]}
        rendered = composite_contract_details(contract)
        for phrase in ["articles", "名称＋真实链接", "至少 8 条", "同条目字段：date",
                       "路径：/article/\\d+"]:
            self.assertIn(phrase, rendered)
        metrics = {"composite_acceptance_pass": False, "composite_group_results": [{
            "id": "articles", "kind": "named_link", "count": 7, "minimum": 8,
            "records": [{"title": "One", "url": "https://example.test/article/1"}],
            "pass": False,
        }]}
        evidence = composite_result_evidence(metrics)
        for phrase in ["有效条目", "至少要求", "7", "8", "未通过", "One"]:
            self.assertIn(phrase, evidence)
        c = case("composite", "body", "risk_control")
        c["acceptance_contract"] = {
            "version": 1, "schema": "body_text", "minimum_records": 0,
            "stable_text_anchors": [],
        }
        r = record(c, passed=False)
        r["metrics"].update(metrics, stable_task_pass=True, stable_content_pass=True)
        self.assertEqual(reason(r, c), "组合规则未满足：articles。")
        self.assertEqual(failure_category(r, c), "规则不满足")

    def test_risk_control_failure_categories_are_exclusive_and_output_first(self):
        c = case("risk", "body", "risk_control")

        def failed(**metrics):
            r = record(c, passed=False)
            r["metrics"].update(forbidden_requirement_hits=[], request_ok=True, content_chars=500,
                                stable_task_pass=False)
            r["metrics"].update(metrics)
            return r

        self.assertEqual(
            failure_category(failed(blocked_page=True, composite_acceptance_pass=False), c),
            "返回验证或拦截页面",
        )
        for track in ("risk_control", "web", "challenge", "popular"):
            qualified_case = case("qualified", "body", track)
            qualified = record(qualified_case, passed=True)
            qualified["metrics"].update(forbidden_requirement_hits=[], blocked_page=True, stable_blocked_page=True)
            self.assertEqual(failure_category(qualified, qualified_case), "—")
        self.assertEqual(failure_category(failed(content_chars=0), c), "完全空返回")
        self.assertEqual(
            failure_category(failed(content_chars=37, markdown_headings=0, markdown_links=0,
                                    stable_anchor_hits=0, content_check_hits=0), c),
            "只返回等待提示或页面外壳",
        )
        self.assertEqual(
            failure_category(failed(composite_acceptance_pass=False), c),
            "规则不满足",
        )

    def test_request_failure_reason_precedes_derived_empty_body_metrics(self):
        c=case('document','body','document');r=record(c,passed=False,ok=False)
        r['metrics']['body_region_results']=[{'coverage':0,'target_pass':False}]
        r['metrics']['body_coverage_pass']=False
        r['diagnosis']={'evidence':'CLI 请求失败：curl request failed'}
        self.assertEqual(reason(r,c),'CLI 请求失败：curl request failed')

    def test_quote_inline_whitespace_loss_exposes_invalid_tag_boundaries(self):
        anchor={'id':'fixed','alternatives':['“An independent thought.”\nby Some Author\nTags: first second']}
        metrics=evaluate_stable_task(
            '“An independent thought.”bySome AuthorTags:firstsecond',
            {'version':1,'schema':'quote','minimum_records':1,'maximum_records':1,
             'stable_text_anchors':[anchor]})
        self.assertTrue(metrics['stable_structure_pass'])
        self.assertFalse(metrics['stable_content_pass'])
        rendered=stable_anchor_evidence(metrics,'“An independent thought.”bySome AuthorTags:firstsecond')
        for phrase in ['An independent thought.','Some Author','firstsecond','字段边界不合格',
                       '文字齐全，但分隔丢失']:
            self.assertIn(phrase,rendered)

    def test_result_review_page_prioritizes_rounds_over_attempt_dump(self):
        c=case();r=record(c,passed=False);r['provider']='moli';r['attempt_id']='moli__list__r001'
        audit={'reviewed_attempts':1,'expected_attempts':1,'disagreements':0,'unresolved':0,
               'reviewer':{'agent':'Codex','model':'test','effort':'high'},
               'review_protocol':{'scope':'all'},
               'attempts':[{'attempt_id':r['attempt_id'],'machine_verdict':'fail',
                            'agent_verdict':'fail','final_verdict':'fail','agent_evidence':'specific evidence'}]}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);(out/'result-review.json').write_text(json.dumps(audit))
            with patch('harness.web_fetch.report_methods.render_methods',return_value='methods'):
                write_support(out,[r],[])
            rendered=(out/'review/index.html').read_text()
            for phrase in ['当前状态','核验轮次','第 1 轮','核验协议与执行者']:
                self.assertIn(phrase,rendered)
            for phrase in ['代理分歧 0','已裁定 0','未决 0']:
                self.assertIn(phrase,rendered)
            self.assertNotIn('当前误判',rendered)
            self.assertNotIn('全部 1 条逐项核验',rendered)
            self.assertNotIn('机器失败项有证据',rendered)
            self.assertNotIn('核验 JSON',rendered)
            self.assertNotIn('结果核验',(out/'reproduce/index.html').read_text())

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

    def test_all_text_tracks_share_columns_and_text_coverage_semantics(self):
        headers=[]
        for track in ('web','dynamic','challenge','risk_control','document'):
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

    def test_body_without_full_reference_keeps_metrics_unavailable(self):
        for track in ('web','dynamic','challenge','risk_control','document'):
            with self.subTest(track=track):
                c=case('example','body',track)
                c['ground_truth_main_content']=''
                r=record(c)
                rendered=ranking([r],{'example':c},track)
                row=table_cells(rendered,'ranking-'+track)[0]
                self.assertEqual(row[4:7],['—']*3)
                self.assertNotIn('资源命中率',rendered)
                self.assertNotIn('直接文件必须',rendered)

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
        self.assertIn('本类最高：<b>100.0%</b>',rendered)
        self.assertIn('fixture',rendered)
        self.assertNotIn('种方法完整判分',rendered)
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
        positions=[rendered.index('data-tab="'+t+'"') for t in ['web','dynamic','challenge','risk_control','document','web_media','media_file']]
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
            self.assertEqual(json.loads((out/'report-languages.json').read_text())['languages'],['zh'])
            self.assertIn('英文报告未生成',(out/'en/index.html').read_text())
            self.assertFalse((out/'en/attempts').exists())
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['任务结果','列表任务：通过','— / — / —','全文指标无完整参考','标准答案与验收规则','有效条目数 / 声明最少数量','3 / 3']:
                self.assertIn(phrase,detail)
            self.assertNotIn('正文未评测',detail)
            self.assertNotIn('评分方式',detail)
            self.assertIn('100.0%',(out/'index.html').read_text())
            self.assertNotIn('<th>达标率</th>',(out/'datasets/index.html').read_text())
            self.assertIn('../analysis/index.html#inspect?query=list',(out/'datasets/list.html').read_text())
            self.assertNotIn('正文未评测',(out/'datasets/list.html').read_text())
            for page in ['index.html', 'datasets/list.html']:
                self.assertIn('稳定标题、栏目、说明', (out/page).read_text())
            with patch('harness.web_fetch.report_methods.render_methods',return_value='methods'):
                write_support(out,[r],[])
            self.assertIn('task-contracts',(out/'methodology/index.html').read_text())

    def test_body_detail_leads_with_exact_reference_and_output_and_explains_positions(self):
        c=case('article','body','document')
        c['ground_truth_main_content']='alpha beta gamma delta'
        c['body_requirements']={'version':3,'unit':'region_compact_hexagram_recall','minimum_coverage':.8,'minimum_floor':.6,
                                'minimum_region_passes':1,
                                'regions':[{'id':'body-region-1','text':'alpha beta gamma delta'}]}
        r=record(c);r['metrics']['body_region_results']=[{'id':'body-region-1','coverage':1,
            'minimum':.8,'floor':.6,'pass':True,'target_pass':True,'floor_pass':True,
            'expected_units':12,'matched_units':12}]
        r['provider_metadata']={'document_extractor':'pypdf'}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True)
            artifact.write_text('alpha beta gamma delta')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[],include_english=True)
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            self.assertLess(detail.index('标准答案与实际返回'),detail.index('为什么这样判'))
            self.assertIn('alpha beta gamma delta',detail)
            for phrase in ['正文判定：', '每段至少达到 60.0%',
                           '本段覆盖率为 100.0%', '参与本段计分的完整实际返回行',
                           '查看各段完整标准正文和计分行',
                           '查看判定使用的标准答案', '查看本次完整实际返回']:
                self.assertIn(phrase,detail)
            self.assertNotIn('定位样例（仅供人工查看）',detail)
            self.assertNotIn('这项分数怎样计算',detail)
            self.assertEqual(detail.count('data-kind="region-output"'),1)
            self.assertIn('方法直接取得原始文档 → pypdf → 可读正文',detail)
            self.assertIn('标准答案位置',detail)
            self.assertNotIn('第 1/1 段',detail)
            english=(out/'en/attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['Body-text rule:', 'Every region must reach at least 60.0%',
                           'Complete actual-output lines used to score this region']:
                self.assertIn(phrase,english)
            dataset=(out/'datasets/article.html').read_text()
            self.assertIn('同一份标准答案',dataset)
            self.assertNotIn('字符六元组数',dataset)

    def test_attempt_call_record_explains_empty_output_and_hides_private_envelope(self):
        c=case('empty','body','document');r=record(c,passed=False,ok=False)
        r.update(error_type='empty_content',error_message='Cloudflare returned no extractable markdown',
                 completed_at='2026-09-03T00:00:01Z')
        r['provider_metadata']={'route':'browser_rendering_markdown','upstream_status':200,
                                'empty_content':True,'raw_response_path':'private_artifacts/secret.bin',
                                'private_artifacts':[{'path':'private_artifacts/secret.bin'}]}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True)
            artifact.write_text('')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[],include_english=True)
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            english=(out/'en/attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['href="#run-log"','id="run-log"','查看运行日志与证据',
                           '运行日志与证据','调用状态','查看完整运行字段','查看本次真实日志',
                           '这次历史调用没有保存可展示的 stderr / debug 日志','原始运行证据']:
                self.assertIn(phrase,detail)
            self.assertNotIn('data-call-log-open="call-log-dialog"',detail)
            self.assertNotIn('<dialog class="call-log-dialog" id="call-log-dialog"',detail)
            self.assertNotIn('private_artifacts/secret.bin',detail)
            self.assertNotIn('<pre data-kind="output"></pre>',detail)
            for phrase in ['View run logs and evidence','Run logs and evidence','Call status',
                           'View complete run fields','View this run\'s real log',
                           'This historical call retained no displayable stderr/debug log']:
                self.assertIn(phrase,english)

    def test_attempt_call_record_uses_hash_bound_native_log_when_available(self):
        c=case('logged','body','web');r=record(c)
        log=json.dumps({'process':{'stderr':'event=request.start\nevent=response.complete\n',
                                   'stderr_truncated':False,'exit_code':0}}).encode()
        r['call_log_path']='artifacts/fixture/logged/run-001.call.log'
        r['call_log_sha256']=hashlib.sha256(log).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True)
            artifact.write_text('output')
            log_path=out/r['call_log_path'];log_path.write_bytes(log)
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[])
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            self.assertIn('查看本次真实日志',detail)
            self.assertIn('<ol class="log-lines">',detail)
            self.assertIn('event=request.start',detail)
            self.assertIn('下载调用日志 JSON',detail)
            self.assertIn(r['call_log_sha256'],detail)
            self.assertFalse((out/'public-logs').exists())

    def test_attempt_call_record_renders_every_retry_log(self):
        c=case('retried','body','web');r=record(c)
        logs=[]
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True)
            artifact.write_text('output')
            for index,stderr in enumerate(('ERROR first physical call','final_url=https://example.com/retried'),1):
                raw=json.dumps({'process':{'stderr':stderr,'exit_code':index-1}}).encode()
                relative=f'call-logs/fixture/retried/physical-{index}.json'
                path=out/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                logs.append({'physical_attempt':index,
                             'call_log':{'path':relative,'sha256':hashlib.sha256(raw).hexdigest(),
                                         'bytes':len(raw)}})
            r['provider_metadata']={'retry':{'attempts':logs}}
            rendered=run_log_section(out,r,'output')
            for phrase in ('物理调用 1','物理调用 2','ERROR first physical call',
                           'final_url=https://example.com/retried','physical-1.json','physical-2.json'):
                self.assertIn(phrase,rendered)
            self.assertFalse((out/'public-logs').exists())

    def test_failure_log_exposes_complete_runtime_error_and_actual_method_identity(self):
        r=record(case());r.update(ok=False,error_type='timeout',error_message='Full runtime error '+('details '*100),
            provider_metadata={'browser_version':'155.0.8059.39','binary_sha256':'actual-hash'})
        with tempfile.TemporaryDirectory() as directory:
            rendered=run_log_section(Path(directory),r,'')
        self.assertIn(r['error_message'],rendered)
        self.assertIn('<h3>程序错误</h3>',rendered)
        self.assertIn('timeout',rendered)
        self.assertIn('155.0.8059.39',rendered)
        self.assertIn('actual-hash',rendered)
        self.assertNotIn('Moli 版本',rendered)
        self.assertNotIn('Markdown 输出',rendered)

    def test_run_log_section_uses_supplied_output_when_artifact_path_is_absent(self):
        c=case('unpersisted','body','web');r=record(c);r.pop('artifact_path')
        with tempfile.TemporaryDirectory() as directory:
            rendered=run_log_section(Path(directory),r,'fallback output')
        self.assertIn('15 字节',rendered)
        self.assertIn('没有可下载的原始记录',rendered)

    def test_generate_writes_every_provider_page_without_moli_version_metadata(self):
        c=case('providers','body','web')
        first=record(c);first['provider']='fixture-a';first['provider_type']='fixture'
        first['attempt_id']='fixture-a__providers__r001'
        first['artifact_path']='artifacts/fixture-a/providers/run-001.txt'
        second=copy.deepcopy(first);second['provider']='fixture-b'
        second['attempt_id']='fixture-b__providers__r001'
        second['artifact_path']='artifacts/fixture-b/providers/run-001.txt'
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            for row in (first,second):
                artifact=out/row['artifact_path'];artifact.parent.mkdir(parents=True,exist_ok=True)
                artifact.write_text('output')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[first,second],[])
            for name in ('fixture-a','fixture-b'):
                page=out/'providers'/f'{name}.html'
                self.assertTrue(page.is_file(),name)
                self.assertIn('1 条执行记录',page.read_text())

    def test_attempt_log_drilldown_uses_canonical_call_log_stderr_in_both_languages(self):
        c=case('debugged','body','web');r=record(c,passed=False,ok=False)
        payload={'request':{'route':'fixture'},
                 'process':{'stdout':'adapter started','stderr':'ERROR challenge page returned'},
                 'error':{'type':'challenge','message':'challenge page returned'},
                 'events':[{'stage':'call_started','error_type':None},
                           {'stage':'call_completed','error_type':'challenge'}]}
        debug=json.dumps(payload,ensure_ascii=False).encode()
        debug_path='call-logs/fixture/debugged/run-001.json'
        r['error_type']='challenge';r['error_message']='challenge page returned'
        r['provider_metadata']={'call_log':{'path':debug_path,
                                            'sha256':hashlib.sha256(debug).hexdigest(),
                                            'bytes':len(debug)}}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True)
            artifact.write_text('output')
            log=out/debug_path;log.parent.mkdir(parents=True);log.write_bytes(debug)
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[],include_english=True)
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            self.assertIn('ERROR challenge page returned',detail)
            self.assertIn('../'+debug_path,detail)
            self.assertIn(hashlib.sha256(debug).hexdigest(),detail)
            for phrase in ['查看完整运行字段','查看本次真实日志','下载调用日志 JSON']:
                self.assertIn(phrase,detail)
            english=(out/'en/attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['View complete run fields','View this run\'s real log',
                           'ERROR challenge page returned','Download call-log JSON']:
                self.assertIn(phrase,english)
            for phrase in ['查看本次真实日志','下载已保存的公开日志']:
                self.assertNotIn(phrase,english)

    def test_attempt_log_drilldown_keeps_full_debug_stderr_out_of_summary(self):
        c=case('debug-stream','body','web');r=record(c)
        stderr='\n'.join(['DEBUG page started','WARN optional resource failed','DEBUG page finished'])
        payload={'request':{'route':'fixture'},'process':{'stderr':stderr}}
        debug=json.dumps(payload,ensure_ascii=False).encode()
        debug_path='call-logs/fixture/debug-stream/run-001.json'
        r['provider_metadata']={'call_log':{'path':debug_path,
                                            'sha256':hashlib.sha256(debug).hexdigest(),
                                            'bytes':len(debug)}}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True)
            artifact.write_text('output')
            log=out/debug_path;log.parent.mkdir(parents=True);log.write_bytes(debug)
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[])
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            first_level=detail.split('<details class="diagnostic-log">',1)[0]
            self.assertIn('这次调用正常形成了可评分输出。',first_level)
            self.assertNotIn('WARN optional resource failed',first_level)
            self.assertIn('WARN optional resource failed',detail)
            self.assertEqual(detail.count('<details class="diagnostic-log">'),1)
            for phrase in ['查看错误与关键日志','查看并下载完整保存证据','查看重试与物理调用']:
                self.assertNotIn(phrase,detail)

    def test_minimal_document_scope_is_explicit_and_does_not_claim_long_document_coverage(self):
        c=case('minimal','body','document')
        c['ground_truth_main_content']='Dummy PDF file'
        self.assertIn('最小文档',task_instruction(c))
        self.assertNotIn('多页',task_instruction(c).split('不代表')[0])
        r=record(c)
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True)
            artifact.write_text('Dummy PDF file')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[])
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['冻结源文件的全部可提取正文共 14 个字符', '标准答案没有省略',
                           '只验证短文档文字提取', '不用于证明多页、表格或附录完整性']:
                self.assertIn(phrase,detail)

    def test_file_detail_compares_expected_file_with_delivered_bytes_not_empty_markdown(self):
        c=case('image','file','image');c['reference_answer']='PNG 图片，8 字节。'
        c['source_verification']={'file_sha256':'abc123'}
        r=record(c);r['artifact_path']='artifacts/fixture/image/run-001.txt'
        r['provider_metadata']={'file_evaluation':{'files':[{'artifact_path':'artifacts/fixture/image/file.bin',
            'mime':'image/png','bytes':8,'sha256':'abc123','pass':True}]}}
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True);artifact.write_text('')
            delivered=out/'artifacts/fixture/image/file.bin';delivered.write_bytes(b'pngbytes')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[])
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['目标文件','本任务验收原始文件字节，不要求 Markdown','image/png','8','abc123','匹配']:
                self.assertIn(phrase,detail)
            self.assertNotIn('本次没有交付可校验的原始文件字节',detail)

    def test_target_only_resource_reason_and_details_show_scope_failures_in_both_languages(self):
        from harness.web_fetch.failure_diagnosis import diagnose

        target = 'https://cdn.example.com/target.jpg'
        alias = 'https://cdn.example.com/target-640.jpg'
        unexpected = 'https://cdn.example.com/recommended.jpg'
        scenarios = [
            ('unexpected-only', True, 1.0, .5, [unexpected],
             '已找到目标资源，但答案字段还返回了非目标资源：',
             '目标资源已命中，但答案字段还返回了非目标资源：',
             'The target resource was found, but answer-bearing fields also returned unexpected resources:'),
            ('mixed', False, 0.0, 0.0, [unexpected],
             '未找到目标资源：target；同时返回非目标资源：',
             '未命中资源：target；同时返回非目标资源：',
             'Missing resources: target; also returned unexpected resources:'),
            ('clean-alias', True, 1.0, 1.0, [],
             '已找到目标资源。', '已找到目标资源。',
             'The required resource was found.'),
        ]
        cases=[];records=[]
        for identifier, matched, recall, precision, extra, zh_reason, _, _ in scenarios:
            c=case(identifier,'resource','image')
            c.update(
                expected_resources=[{'id':'target','kind':'image','urls':[target],'aliases':[alias]}],
                resource_output_contract={'candidate_scope':'image_answer_fields','unexpected':'reject'},
                reference_answer='返回登记的目标图片或允许别名，且不返回其它图片。',
            )
            r=record(c,passed=matched and not extra)
            matched_urls=[alias] if identifier=='clean-alias' else [target] if matched else []
            candidate_urls=matched_urls+extra
            r['metrics'].update(
                resource_pass=matched and not extra,
                resource_hits=1 if matched else 0,
                resource_total=1,
                resource_recall=recall,
                resource_precision=precision,
                resource_results=[{'id':'target','kind':'image','expected_urls':[target,alias],
                                   'expected_mime_types':[],'matched_urls':matched_urls,'pass':matched}],
                resource_candidate_urls=candidate_urls,
                resource_candidates=[{'url':url,'sources':['metadata.images']}
                                     for url in candidate_urls],
                unexpected_resource_urls=extra,
            )
            fallback=reason(r,c)
            self.assertIn(zh_reason,fallback)
            if extra:self.assertIn(unexpected,fallback)
            if not r['metrics']['resource_pass']:
                r['diagnosis']=diagnose(r,c,'')
            cases.append(c);records.append(r)

        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            for r in records:
                artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True,exist_ok=True)
                artifact.write_text('\n'.join(r['metrics']['resource_candidate_urls']))
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), \
                    patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':cases},records,[],include_english=True)
            for (identifier, matched, recall, precision, extra, _, zh_detail, en_reason),r in zip(scenarios,records):
                zh=(out/'attempts'/f'{r["attempt_id"]}.html').read_text()
                en=(out/'en/attempts'/f'{r["attempt_id"]}.html').read_text()
                expected_values=f'{recall*100:.1f}% / {precision*100:.1f}%'
                self.assertIn('资源召回率 / 纯净度',zh)
                self.assertIn('Resource recall / precision',en)
                self.assertIn(expected_values,zh)
                self.assertIn(expected_values,en)
                self.assertIn(zh_detail,zh)
                self.assertIn(en_reason,en)
                for phrase in ['候选资源地址','答案字段来源','范围判定','metadata.images']:
                    self.assertIn(phrase,zh)
                for phrase in ['Candidate resource URL','Answer-field source','Scope decision','metadata.images']:
                    self.assertIn(phrase,en)
                expected_verdict='通过' if r['metrics']['resource_pass'] else '未通过'
                self.assertIn(f'<span class="status-line">{expected_verdict}</span>',zh)
                self.assertIn(f'<span class="status-line">{"Pass" if expected_verdict=="通过" else "Fail"}</span>',en)
                if extra:
                    self.assertIn('非目标资源',zh)
                    self.assertIn('Unexpected resources',en)
                    self.assertIn(unexpected,zh)
                    self.assertIn(unexpected,en)
                else:
                    self.assertNotIn('非目标资源',zh)
                    self.assertNotIn('Unexpected resources',en)
                    self.assertIn(alias,zh)
                    self.assertIn(alias,en)

    def test_body_failure_reason_names_readable_positions_not_internal_region_ids(self):
        from harness.web_fetch.report import reason
        c=case('article','body','web')
        r=record(c,passed=False)
        r['metrics'].update(body_coverage_pass=False,body_region_results=[
            {'id':f'body-region-{index+1}','coverage':.1 if index==0 else .9,
             'minimum':.8,'pass':index!=0,'target_pass':index!=0}
            for index in range(5)
        ])
        r['diagnosis']={'evidence':'body-region-1'}
        rendered=reason(r,c)
        self.assertIn('开头 10.0%',rendered)
        self.assertNotIn('body-region',rendered)

    def test_current_canonical_challenge_has_zero_body_coverage(self):
        from harness.web_fetch.storage import current_evidence_dir
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
        self.assertIn('稳定文字或记录结构没有满足任务要求',reason(r,c))
        self.assertNotIn('missing_stable_anchor:heading',reason(r,c))

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

    def test_fixed_contract_rules_survive_absent_historical_attachment(self):
        from harness.web_fetch.report_components import stable_contract_details
        c=case();c['acceptance_contract']={'schema':'named_link','minimum_records':3,
            'hosts':['example.com'],'entry_path':'/items/.*',
            'stable_text_anchors':[{'id':'heading','alternatives':['Fixed heading']}]}
        c['source_benchmark']['stable_annotation']={'anchor_evidence':'sources/early.txt'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rendered=stable_contract_details(c,evidence_root=root)
            self.assertIn('Fixed heading',rendered)
            self.assertNotIn('href="../sources/early.txt"',rendered)
            (root/'sources').mkdir();(root/'sources/early.txt').write_text('Fixed heading')
            self.assertIn('href="../sources/early.txt"',stable_contract_details(c,evidence_root=root))

    def test_body_coverage_explanation_uses_the_declared_unit_and_region_rules(self):
        c=case(kind='body',track='web');c['body_requirements']={
            'unit':'region_compact_hexagram_recall','minimum_coverage':.8,
            'minimum_floor':.6,'minimum_region_passes':4,
            'regions':[{'id':str(i),'text':'reference'} for i in range(5)]}
        rendered=task_pass_guide('web',cases={c['id']:c})
        self.assertIn('字符六元组',rendered)
        self.assertIn('通过区域数',rendered)
        self.assertNotIn('词语计数覆盖',rendered)
        self.assertNotIn('逐区达到 80%',rendered)

    def test_schema_fields_are_explained_without_internal_version_or_empty_sections(self):
        from harness.web_fetch.report_components import stable_contract_details, FIELD_REQUIREMENTS
        from lexbench_webfetch.stable_tasks import SCHEMA_FIELDS, UNIQUE_FIELDS
        for schema,fields in SCHEMA_FIELDS.items():
            c=case();c['acceptance_contract']={'version':1,'schema':schema,'minimum_records':3}
            if schema=='named_link':c['acceptance_contract'].update(hosts=['example.com'],entry_path='/items/.*')
            rendered=stable_contract_details(c)
            with self.subTest(schema=schema):
                if schema in {'body_text', 'page_identity'}:
                    self.assertNotIn('必需字段与要求',rendered)
                else:
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

    def test_general_report_explains_rerun_scope_and_packaged_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            with patch('harness.web_fetch.report_methods.render_methods', return_value='methods'):
                write_support(out, [], [])
            methodology = (out/'methodology/index.html').read_text()
            reproduce = (out/'reproduce/index.html').read_text()
        self.assertIn('变更后重跑范围', methodology)
        self.assertIn('只改展示时重建报告', methodology)
        self.assertIn('发布与加载', reproduce)
        self.assertIn('通用 HTML 外壳', reproduce)
        self.assertIn('超过 256 KiB', reproduce)

    def test_real_methodology_pngs_available_before_generate(self):
        from harness.web_fetch.report_build import build_report
        assets=Path(__file__).resolve().parents[1]/'harness/web_fetch/assets/methodology'
        c=case();r=record(c)
        c['acceptance_contract']={'version':1,'schema':'product','minimum_records':1,'stable_text_anchors':[{'id':'title','alternatives':['商品']} ]}
        r['metrics']['acceptance_contract_sha256']=hashlib.sha256(json.dumps(c['acceptance_contract'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        r['metrics'].update(stable_task_pass=True)
        private = b'{"private":"provider envelope"}'
        r['provider_metadata'] = {
            'raw_response_path': 'private_artifacts/fixture/list/run-001.response.bin',
            'raw_response_sha256': hashlib.sha256(private).hexdigest(),
            'raw_response_bytes': len(private),
        }
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';source.mkdir();out=Path(directory)/'report'
            (source/'dataset.json').write_text(json.dumps({'cases':[c]}))
            (source/'results.jsonl').write_text(json.dumps(r)+'\n')
            (source/'manifest.json').write_text('{}')
            artifact=source/r['artifact_path'];artifact.parent.mkdir(parents=True);artifact.write_bytes(b'output')
            private_path=source/r['provider_metadata']['raw_response_path']
            private_path.parent.mkdir(parents=True);private_path.write_bytes(private)
            def inspect_generate(staging,*args,**kwargs):
                self.assertFalse(kwargs['include_english'])
                for name in ['annotation','validation','scoring']:
                    expected=(assets/f'{name}.png').read_bytes()
                    self.assertTrue(expected.startswith(b'\x89PNG\r\n\x1a\n'))
                    self.assertEqual((staging/f'assets/methodology/{name}.png').read_bytes(),expected)
                summary=json.loads((staging/'summary.json').read_text())
                self.assertEqual(summary['challenge']['fixture']['scoring_scope'],'stable_contract')
                self.assertEqual(summary['challenge']['fixture']['task_pass'],1)
                self.assertFalse((staging/'private_artifacts').exists())
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

    def test_build_rejects_stale_scoring_implementation_before_render(self):
        from harness.web_fetch.report_build import build_report
        c=case();r=record(c);r['metrics']['scoring_source_sha256']='old-scorer'
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';source.mkdir();out=Path(directory)/'report'
            (source/'dataset.json').write_text(json.dumps({'cases':[c]}))
            (source/'results.jsonl').write_text(json.dumps(r)+'\n')
            with patch('harness.web_fetch.report_build.generate') as generated:
                with self.assertRaisesRegex(ValueError,'Stale scoring implementation'):
                    build_report(source,out)
            generated.assert_not_called()
            self.assertFalse(out.exists())


if __name__=='__main__':
    unittest.main()


def test_body_display_shares_native_markup_and_entity_handling_with_scorer():
    from lexbench_webfetch.scoring import score_attempt
    contract = {"version": 3, "unit": "region_compact_hexagram_recall",
                "minimum_coverage": .8, "minimum_floor": .6, "minimum_region_passes": 1,
                "regions": [{"id": "body", "text": "abcdefghijkl"}]}
    definition = {"id": "markup", "url": "https://example.test", "body_requirements": contract}
    for output in ("abcdef<br>ghijkl", "abcdef&#103;hijkl", "`<br>` abcdefghijkl",
                   "\\<br> abcdefghijkl", "<main>abcdef<script>ghijkl</script></main>",
                   'abcdef <a href="https://example.test/ghijkl">source</a>',
                   'abcdef <img src="https://example.test/ghijkl" alt="photo">'):
        record = {"ok": True, "title": "abcdefghijkl"}
        metrics = score_attempt(definition, output.encode(), ok=True, title=record["title"])
        display = _body_scoring_text(record, output)
        rows, cards = body_region_rows(definition, metrics, display)
        assert len(rows) == 1
        assert "<script>" not in display
        if output.startswith(("`", "\\")):
            assert "<br>" in display


def test_frozen_body_details_preserve_saved_allocation_and_redact_excerpts():
    from lexbench_webfetch.body_coverage import evaluate_body_requirements
    contract = {"version": 3, "unit": "region_compact_hexagram_recall", "minimum_coverage": .8, "minimum_floor": .6,
                "minimum_region_passes": 1, "regions": [{"id": "body", "text": "abcdefghijkl"}]}
    definition = {"body_requirements": contract}
    metrics = evaluate_body_requirements("abcdefghijkl https://example.test/?access_token=secret123456", contract)
    original = copy.deepcopy(metrics)
    with unittest.TestCase().assertRaisesRegex(ValueError, 'Saved body evidence differs'):
        body_region_rows(definition, metrics, 'changed public output')
    rows, cards = body_region_rows(definition, metrics, 'changed public output', frozen_scores=True)
    assert len(rows) == 1
    assert 'abcdefghijkl' in ''.join(cards)
    assert 'secret123456' not in ''.join(cards)
    assert metrics == original
    for region in metrics['body_region_results']:
        region.pop('matched_output_passages')
    with patch('lexbench_webfetch.body_coverage.evaluate_body_requirements', side_effect=AssertionError('Must not rescore')):
        _, cards = body_region_rows(definition, metrics, 'different output', frozen_scores=True)
    assert '历史评分未保存' in ''.join(cards)
