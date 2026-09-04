import unittest

from harness.web_fetch.reliability import assess_reliability, require_reliable


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.dataset={
            'cases':[{'id':'a','track':'web','task_kind':'body'}],
            'coverage_plan':{'web':['p']},
        }
        self.record={'provider':'p','case_id':'a','metrics':{'task_kind':'body'}}

    def test_complete_plan_is_reliable(self):
        result=assess_reliability(self.dataset,[self.record])
        self.assertTrue(result['complete'])
        self.assertEqual(result['judged_calls'],1)

    def test_missing_duplicate_and_unjudged_are_rejected(self):
        self.assertFalse(assess_reliability(self.dataset,[])['complete'])
        duplicate=assess_reliability(self.dataset,[self.record,self.record])
        self.assertFalse(duplicate['complete'])
        self.assertTrue(duplicate['duplicates'])
        unjudged={**self.record,'metrics':{'task_kind':'file'}}
        result=assess_reliability({**self.dataset,'cases':[{'id':'a','track':'web','task_kind':'file'}]},[unjudged])
        self.assertFalse(result['complete'])
        self.assertTrue(result['unjudged'])
        with self.assertRaisesRegex(ValueError,'not publishable'):
            require_reliable(self.dataset,[])


if __name__=='__main__':unittest.main()
