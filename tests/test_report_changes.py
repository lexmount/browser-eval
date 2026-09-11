import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from harness.web_fetch.report_changes import render_changes
from harness.web_fetch.report import overview_card, ranking

class ReportChangesTests(unittest.TestCase):
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
        page=ranking([r],{'file':c},'video')
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
