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
