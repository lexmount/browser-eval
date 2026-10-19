import unittest
from harness.web_fetch.report import reason


class FileReportReasonTests(unittest.TestCase):
    def test_success_is_not_described_as_missing_bytes(self):
        row={'ok':True,'metrics':{'task_kind':'file','raw_file_pass':True}}
        self.assertIn('校验通过',reason(row,{'task_kind':'file'}))
        self.assertNotIn('没有返回',reason(row,{'task_kind':'file'}))

    def test_failed_and_unmeasured_remain_distinct(self):
        row={'ok':True,'metrics':{'task_kind':'file','raw_file_pass':False}}
        self.assertIn('没有返回',reason(row,{'task_kind':'file'}))
        row['metrics'].pop('raw_file_pass')
        self.assertIn('未测',reason(row,{'task_kind':'file'}))

    def test_late_bytes_do_not_get_success_explanation(self):
        row={'ok':True,'metrics':{'task_kind':'file','raw_file_pass':True,'deadline_overrun':True}}
        self.assertIn('超过',reason(row,{'task_kind':'file'}))

    def test_missing_page_identity_is_not_a_file_or_uncalled_result(self):
        from harness.web_fetch.report import failure_category, result_label
        row={'ok':True,'metrics':{'request_ok':True,'page_identity_required':True,
                                'page_identity_status':'unavailable'}}
        self.assertEqual(result_label(row,{}),'无法判定')
        self.assertIn('最终网页身份',reason(row,{}))
        self.assertNotIn('原文件',reason(row,{}))
        self.assertIn('无法判定',failure_category(row,{}))

    def test_shared_evidence_lines_preserve_complete_exact_matches(self):
        from harness.web_fetch.report import _exact_evidence_lines, _normalized_evidence_lines
        output='  A &amp; B  \nUnrelated\nA & B\nA & B\n'
        cached=_normalized_evidence_lines(output)
        for expected in ['A & B','Unrelated','missing','']:
            self.assertEqual(_exact_evidence_lines(expected,output),
                             _exact_evidence_lines(expected,output,normalized_lines=cached))
        self.assertEqual(_exact_evidence_lines('Unrelated',output,normalized_lines=cached),'Unrelated')
        self.assertEqual(_exact_evidence_lines('missing',output,normalized_lines=cached),'')

    def test_unknown_result_is_visible_but_not_in_acceptance_denominator(self):
        from harness.web_fetch.report import overall_ranking
        cases={'a':{'id':'a','track':'web'},'b':{'id':'b','track':'web'}}
        rows=[{'provider':'p','case_id':'a','ok':True,'elapsed_ms':1,
               'metrics':{'content_pass':True}},
              {'provider':'p','case_id':'b','ok':True,'elapsed_ms':1,
               'metrics':{'page_identity_required':True,'page_identity_status':'unavailable'}}]
        rendered=overall_ranking(rows,cases)
        self.assertIn('无法判定',rendered)
        self.assertIn('1/1',rendered)
        self.assertIn('可判定数',rendered)
        self.assertNotIn('每题计一次通过或失败',rendered)


class BoundedAnchorEvidenceTests(unittest.TestCase):
    def test_single_line_html_does_not_repeat_the_answer_for_each_anchor(self):
        from harness.web_fetch.report import stable_anchor_evidence
        from bs4 import BeautifulSoup
        labels = ['first anchor', 'middle anchor', 'last anchor']
        output = '<div>' + ('x' * 200000).join(labels) + '</div>'
        metrics = {'stable_anchor_results': [dict(alternatives=[label], matched=[label], **{'pass': True})
                                             for label in labels]}
        rendered = stable_anchor_evidence(metrics, output)
        cards = BeautifulSoup(rendered, 'html.parser').select('.anchor-card')
        self.assertEqual(len(cards), 3)
        for card, label in zip(cards, labels):
            fragment = card.select('.evidence-sample')[1].p.get_text()
            self.assertIn(label, fragment)
            self.assertIn(fragment, output)
            self.assertLessEqual(len(fragment), 1200)
        self.assertLess(len(rendered), 10000)

    def test_window_boundary_keeps_the_actual_match_and_rejects_missing_text(self):
        from harness.web_fetch.report import _exact_evidence_lines, stable_anchor_evidence
        expected = 'boundary anchor' * 20
        output = 'x' * 1100 + expected + 'y' * 20000
        excerpt = _exact_evidence_lines(expected, output)
        self.assertIn(expected, excerpt)
        self.assertIn(excerpt, output)
        self.assertLessEqual(len(excerpt), 1200)
        self.assertEqual(_exact_evidence_lines('not present', output), '')
        rendered = stable_anchor_evidence({'stable_anchor_results': [dict(
            alternatives=['not present'], matched=['not present'], **{'pass': True})]}, output)
        self.assertIn('未找到可供定位的连续原文', rendered)
        self.assertNotIn('<p translate="no">not present</p>', rendered)

    def test_chunking_cannot_invent_a_match_from_a_long_link_destination(self):
        from harness.web_fetch.report import _exact_evidence_lines
        output = '[visible](https://example.test/' + 'x' * 2000 + 'destination_only' + 'y' * 2000 + ')'
        self.assertEqual(_exact_evidence_lines('destination_only', output), '')

    def test_template_change_refreshes_also_unchanged_retained_result_pages(self):
        from harness.web_fetch.report_build import _presentation_detail_scope
        records = [{'attempt_id': 'fresh'}, {'attempt_id': 'retained'}]
        self.assertEqual(_presentation_detail_scope(records, {'fresh'},
            {'presentation_refresh': {'renderer_sha256': 'old'}}, 'new'), {'fresh', 'retained'})
        self.assertEqual(_presentation_detail_scope(records, {'fresh'},
            {'presentation_refresh': {'renderer_sha256': 'new'}}, 'new'), {'fresh'})
        self.assertEqual(_presentation_detail_scope(records, {'fresh'}, {}, 'new'), {'fresh', 'retained'})
