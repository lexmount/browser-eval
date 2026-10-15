import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from harness.web_fetch.report_changes import render_changes, render_webmainbench_history, webmainbench_history
from harness.web_fetch.report import overview_card, ranking

class ReportChangesTests(unittest.TestCase):
    def test_unpublished_corrections_are_not_public_history_or_revision_bindings(self):
        from harness.web_fetch.report_changes import select_cohort_history, require_revision_release_bindings
        public={'id':'public','publication_state':'published','comparison_kinds':['headless_markdown'],'scoring_revision_input_sha256':'published'}
        draft={'id':'draft','comparison_kinds':['headless_markdown'],'publication_state':'unpublished','scoring_revision_input_sha256':'draft'}
        data={'schema_version':3,'releases':[draft,public],'review_rounds':[{'id':'review','description':'Synthetic review'}]}
        with patch('harness.web_fetch.report_changes.require_review_rounds',return_value=[]):
            selected=select_cohort_history(data,{'comparison':{'kind':'headless_markdown'}},[])
        self.assertEqual(selected['releases'],[public])
        unmarked={k:v for k,v in public.items() if k!='publication_state'}
        with patch('harness.web_fetch.report_changes.require_review_rounds',return_value=[]), self.assertRaisesRegex(ValueError,'publication state'):
            select_cohort_history({**data,'releases':[unmarked]}, {'comparison':{'kind':'headless_markdown'}}, [])
        require_revision_release_bindings([{'input_results_sha256':'published'}],[draft,public])
        with self.assertRaises(ValueError):
            require_revision_release_bindings([{'input_results_sha256':'draft'}],[draft,public])
        with self.assertRaisesRegex(ValueError,'publication state'):
            require_revision_release_bindings([{'input_results_sha256':'published'}],[{**public,'publication_state':'typo'}])

    def test_webmainbench_history_is_localized_from_one_record(self):
        from harness.web_fetch.report_changes import ROOT
        source = json.loads((ROOT / 'research/report-changes.json').read_text(encoding='utf-8'))
        releases = webmainbench_history(source)
        expected = [row for row in source['releases']
                    if 'webmainbench_frozen_html_to_markdown' in row.get('comparison_kinds', [])]
        self.assertEqual([row['id'] for row in releases], [row['id'] for row in expected])
        for english in (False, True):
            page = render_webmainbench_history(
                {'releases': releases}, english,
                [('latency-corrections.json', '纠正收据', 'Correction receipt')])
            self.assertEqual(page.count('data-release-id='), len(releases))
            latest = releases[0]['i18n']['en'] if english else releases[0]
            self.assertIn(latest['summary'], page)
            self.assertIn('latency-corrections.json', page)
        broken = json.loads(json.dumps(source))
        first = next(row for row in broken['releases'] if row['id'] == releases[0]['id'])
        first['i18n']['en'].pop('summary')
        with self.assertRaisesRegex(ValueError, 'bilingual summary'):
            webmainbench_history(broken)

    def test_web_fetch_release_translations_come_from_history(self):
        from harness.web_fetch.report_changes import ROOT
        from harness.web_fetch.report_i18n import release_translations, translate_text
        data = json.loads((ROOT / 'research/report-changes.json').read_text(encoding='utf-8'))
        from harness.web_fetch.report_changes import select_cohort_history
        kinds = ('generic', 'headless_markdown', 'web_fetch_methods',
                 'uniform_native_local_measurement', 'single_provider_dynamic_native_fetch',
                 'risk_control_100_moli')
        releases = {row['id']: row for kind in kinds
                    for row in select_cohort_history(data, {'comparison': {'kind': kind}}, [])['releases']}
        self.assertTrue(releases)
        translations = release_translations({"releases": list(releases.values())})
        for row in releases.values():
            english = row['i18n']['en']
            for key in ('title', 'summary'):
                self.assertEqual(translate_text(row[key], translations), english[key], row['id'])
            self.assertEqual(len(row['changes']), len(english['changes']))
            for source, target in zip(row['changes'], english['changes'], strict=True):
                for zh, en in zip(source, target, strict=True):
                    self.assertEqual(translate_text(zh, translations), en, row['id'])

    def test_release_translation_reuse_requires_one_unambiguous_english_value(self):
        from harness.web_fetch.report_i18n import release_translations, translate_text
        release = {
            'id': 'first', 'title': '版本', 'summary': '摘要',
            'changes': [['项目', '原问题', '修正', '边界']],
            'i18n': {'en': {'title': 'Release', 'summary': 'Summary',
                           'changes': [['Item', 'Prior issue', 'Correction', 'Limits']]}},
        }
        matching = json.loads(json.dumps(release))
        matching['id'] = 'second'
        translations = release_translations({'releases': [release, matching]})
        self.assertEqual(translate_text('修正', translations), 'Correction')
        for field in ('title', 'summary', 'changes'):
            conflicting = json.loads(json.dumps(matching))
            if field == 'changes':
                conflicting['i18n']['en']['changes'][0][2] = 'Different correction'
            else:
                conflicting['i18n']['en'][field] = 'Different English text'
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, 'Conflicting English release translation'
            ):
                release_translations({'releases': [release, conflicting]})

    def test_dynamic_local_audit_release_is_selected_only_for_its_cohort(self):
        from harness.web_fetch.report_changes import ROOT, select_cohort_history
        data = json.loads((ROOT / 'research/report-changes.json').read_text(encoding='utf-8'))
        dynamic = select_cohort_history(
            data, {'comparison': {'kind': 'single_provider_dynamic_native_fetch'}}, [])
        generic = select_cohort_history(data, {'comparison': {'kind': 'generic'}}, [])
        release_id = '2026-09-24-dynamic-100-moli-119-local-audit'
        self.assertIn(release_id, {row['id'] for row in dynamic['releases']})
        self.assertNotIn(release_id, {row['id'] for row in generic['releases']})

    def test_fixed_native_history_does_not_leak_into_other_cohorts(self):
        from harness.web_fetch.report_changes import ROOT, select_cohort_history
        data = json.loads((ROOT / 'research/report-changes.json').read_text())
        release_id = '2026-10-09-dynamic-1000-native-presentation'
        for kind in ('uniform_native_local_measurement', 'generic',
                     'single_provider_dynamic_native_fetch', 'risk_control_100_moli',
                     'headless_markdown', 'web_fetch_methods'):
            with self.subTest(kind=kind):
                releases = select_cohort_history(data, {'comparison': {'kind': kind}}, [])['releases']
                self.assertEqual(release_id in {row['id'] for row in releases},
                                 kind == 'uniform_native_local_measurement')

    def test_risk_control_release_is_selected_only_for_its_cohort(self):
        from harness.web_fetch.report_changes import ROOT, select_cohort_history
        data = json.loads((ROOT / 'research/report-changes.json').read_text(encoding='utf-8'))
        risk = select_cohort_history(
            data, {'comparison': {'kind': 'risk_control_100_moli'}}, [])
        generic = select_cohort_history(data, {'comparison': {'kind': 'generic'}}, [])
        release_id = '2026-09-29-risk-control-review-corrections'
        self.assertIn(release_id, {row['id'] for row in risk['releases']})
        self.assertNotIn(release_id, {row['id'] for row in generic['releases']})

    def test_current_web_fetch_release_binds_score_revision_and_exact_rerun_scope(self):
        from harness.web_fetch.report_changes import ROOT, select_cohort_history
        from harness.web_fetch.report_i18n import release_translations, translate_text
        history = json.loads((ROOT/'research/report-changes.json').read_text())
        selected = select_cohort_history(
            history, {'comparison': {'kind': 'web_fetch_methods'}}, []
        )
        translations = release_translations(selected)
        current = selected['releases'][0]
        self.assertEqual(current['id'], '2026-09-15-official-route-and-call-drilldown')
        self.assertEqual(set(current['scoring_revision_input_sha256']), {
            '596f8005399be74d73dd14b8a77f5f775b3e2dd36f037643c421259bb5d8ff53',
            'b8b7adec934761ed374191912c05ef2da449cb1a7cbda104f671db08d033a5ee',
        })
        self.assertIn('Cloudflare 23 条、Firecrawl 17 条，共 40 次调用', current['summary'])
        self.assertIn('累计 44 次新物理调用', current['summary'])
        self.assertIn('1,032/1,500', current['summary'])
        self.assertIn('1,500/1,500 条权威结果有有效逐条判定核验',
                      current['changes'][4][3])
        self.assertNotIn('新增异常组', current['summary'])
        for value in [current['title'], current['summary'],
                      *(field for row in current['changes'] for field in row)]:
            self.assertNotRegex(translate_text(value, translations), r'[\u4e00-\u9fff]')

    def test_first_correction_is_identity_bound_not_latest(self):
        from harness.web_fetch.report_changes import first_correction
        releases=[{'id':'2026-09-03-fairness-correction','scoring_revision_input_sha256':'first'}]
        original={'input_results_sha256':'first','records':[]}
        later={'input_results_sha256':'later','records':[1]}
        self.assertEqual(first_correction([later,original],releases),original)
        self.assertEqual(first_correction([original,later],releases),original)
        with self.assertRaises(ValueError):first_correction([later],releases)
        with self.assertRaisesRegex(ValueError, 'fairness-correction'):
            first_correction([original],[])

    def test_file_rerun_sources_are_all_preflighted_before_report_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            out=root/'out';out.mkdir()
            evidence=root/'evidence';evidence.mkdir()
            (out/'file-rerun-selection.json').write_text('{"records": []}')
            (evidence/'file-provider-rerun').mkdir()
            with patch('harness.web_fetch.storage.research_evidence_dir',
                       return_value=evidence):
                with self.assertRaisesRegex(FileNotFoundError, 'file-provider-rerun-native'):
                    render_changes(out,{},[])
            self.assertFalse((out/'report-changes.json').exists())
            self.assertFalse((out/'file-provider-rerun').exists())
    def test_every_bundled_revision_binds_one_release(self):
        from harness.web_fetch.report_changes import require_revision_release_bindings
        history=[{'input_results_sha256':'new'}]
        releases=[{'id':'old','scoring_revision_input_sha256':'old'},
                  {'id':'new','scoring_revision_input_sha256':['new','newer']}]
        require_revision_release_bindings(history,releases)
        with self.assertRaises(ValueError):
            require_revision_release_bindings(history,releases[:1])
        with self.assertRaises(ValueError):
            require_revision_release_bindings(history,releases+[dict(releases[1],id='duplicate')])
    def test_release_history_is_compact_and_reader_facing(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            render_changes(out,{},[])
            page=(out/'changes/index.html').read_text()
            self.assertIn('报告版本说明',page)
            self.assertIn('当前版本',page)
            self.assertIn('36 次文件任务全部实际调用并判定',page)
            self.assertIn('不补编历史',page)
            self.assertIn('<details><summary>',page)
            self.assertNotIn('<details open',page)
            self.assertNotIn('这两列怎么读',page)
            data=json.loads((out/'report-changes.json').read_text())
            self.assertEqual(len(data['releases']),len({r['id'] for r in data['releases']}))
            self.assertEqual(data['review_rounds'],[])
            release_ids={row['id'] for row in data['releases']}
            self.assertIn('2026-09-04-complete-file-evaluation',release_ids)
            self.assertNotIn('2026-09-04-five-browser-fastest-markdown',release_ids)
            self.assertNotIn('github.com/lexmount/browser-eval/pull/6', page)

    def test_review_round_log_is_required_and_structured(self):
        from harness.web_fetch.report_changes import require_review_rounds
        valid={'review_rounds':[{'date':'2026-09-08','title':'复核','scope':'全部结果',
                                 'finding':'发现','fix':'修正','pending':'无'}]}
        self.assertEqual(require_review_rounds(valid), valid['review_rounds'])
        for invalid in ({}, {'review_rounds':[]},
                        {'review_rounds':[{'date':'2026-09-08','title':'复核'}]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                require_review_rounds(invalid)

    def test_release_history_is_scoped_to_the_current_cohort(self):
        from harness.web_fetch.report_changes import select_cohort_history
        data={
            'review_rounds':[{'date':'d','title':'t','scope':'s','finding':'f','fix':'x','pending':'p'}],
            'releases':[
                {'id':'new-headless'},
                {'id':'2026-09-04-five-browser-fastest-markdown'},
                {'id':'generic-file'},
                {'id':'generic-score'},
            ],
        }
        headless=select_cohort_history(data,{'comparison':{'kind':'headless_markdown'}},[])
        generic=select_cohort_history(data,{'comparison':None},[{'provider_type':'firecrawl'}])
        self.assertEqual([r['id'] for r in headless['releases']],
                         ['new-headless','2026-09-04-five-browser-fastest-markdown'])
        self.assertEqual([r['id'] for r in generic['releases']],
                         ['generic-file','generic-score'])
        self.assertTrue(headless['review_rounds'])
        self.assertEqual(generic['review_rounds'],[])

    def test_explicit_comparison_kind_overrides_legacy_partition(self):
        from harness.web_fetch.report_changes import select_cohort_history
        data={
            'review_rounds':[{'date':'d','title':'t','scope':'s','finding':'f','fix':'x','pending':'p'}],
            'releases':[
                {'id':'tagged-generic','comparison_kinds':['generic']},
                {'id':'2026-09-04-five-browser-fastest-markdown'},
            ],
        }
        selected=select_cohort_history(data,{'comparison':{'kind':'generic'}},[])
        self.assertEqual([r['id'] for r in selected['releases']],['tagged-generic'])

    def test_ranking_uses_planned_judgment_denominator(self):
        c={'id':'file','name':'File','track':'video','task_kind':'file'}
        r={'case_id':'file','provider':'p','ok':True,'elapsed_ms':1,'metrics':{'task_kind':'file'}}
        page=ranking([r],{'file':c},'media_file')
        self.assertIn('判分完整性',page)
        self.assertNotIn('已执行/总案例',page)
        self.assertIn('缺少验收证据的案例',page)
        self.assertIn('changes/index.html#file-gap',page)
        self.assertIn('0/1',page)

    def test_overview_names_all_highest_scoring_methods(self):
        cases={'a':{'id':'a','track':'web','task_kind':'body'}}
        records=[
            {'case_id':'a','provider':name,'ok':True,'metrics':{'content_pass':True}}
            for name in ['first','second']
        ]
        page=overview_card(records,cases,'web','网站正文',['first','second'])
        self.assertIn('<p>first、second</p>',page)
        self.assertNotIn('种方法完整判分',page)
