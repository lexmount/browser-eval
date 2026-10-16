import unittest
from lexbench_webfetch.metrics import task_passed
from harness.web_fetch.report import succeeded


class TaskResultTests(unittest.TestCase):
    def test_task_specific_rules_match_report(self):
        examples=[
            ('resource',{'content_pass':False,'resource_pass':True},True),
            ('file',{'content_pass':False,'raw_file_pass':True},True),
            ('structure',{'structure_pass':True,'list_task_pass':False},False),
            ('structure',{'list_task_pass':True},True),
            ('body',{'content_pass':True},True),
        ]
        for kind,metrics,expected in examples:
            with self.subTest(kind=kind,metrics=metrics):
                self.assertEqual(task_passed(True,metrics,kind),expected)
                self.assertEqual(succeeded({'ok':True,'metrics':metrics},{'task_kind':kind}),expected)
                self.assertFalse(task_passed(False,metrics,kind))
