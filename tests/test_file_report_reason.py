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
