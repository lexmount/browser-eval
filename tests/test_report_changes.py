import json
import tempfile
import unittest
from pathlib import Path
from harness.web_fetch.report_changes import render_changes
from harness.web_fetch.report import ranking

class ReportChangesTests(unittest.TestCase):
    def test_first_correction_is_identity_bound_not_latest(self):
        from harness.web_fetch.report_changes import first_correction
        releases=[{'id':'2026-09-03-fairness-correction','scoring_revision_input_sha256':'first'}]
        original={'input_results_sha256':'first','records':[]}
        later={'input_results_sha256':'later','records':[1]}
        self.assertEqual(first_correction([later,original],releases),original)
        self.assertEqual(first_correction([original,later],releases),original)
        with self.assertRaises(ValueError):first_correction([later],releases)
    def test_release_history_has_collapsed_details_and_explicit_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            render_changes(out,{},[])
            page=(out/'changes/index.html').read_text()
            self.assertIn('报告变更记录',page)
            self.assertIn('不是通过数',page)
            self.assertIn('8/10',page)
            self.assertIn('不补编历史',page)
            self.assertIn('<details><summary>',page)
            self.assertNotIn('<details open',page)
            data=json.loads((out/'report-changes.json').read_text())
            self.assertEqual(len(data['releases']),len({r['id'] for r in data['releases']}))

    def test_ranking_explains_missing_denominator_next_to_table(self):
        c={'id':'file','name':'File','track':'video','task_kind':'file'}
        r={'case_id':'file','provider':'p','ok':True,'elapsed_ms':1,'metrics':{'task_kind':'file'}}
        page=ranking([r],{'file':c},'video')
        self.assertIn('已判分/已调用',page)
        self.assertIn('已执行/总案例',page)
        self.assertIn('缺少验收证据的案例',page)
        self.assertIn('changes/index.html#file-gap',page)
        self.assertIn('0/1',page)
