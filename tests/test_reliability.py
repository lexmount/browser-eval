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

    def test_failure_only_reruns_retain_successes_and_disclose_build_counts(self):
        dataset = {**self.dataset, 'cases': [self.dataset['cases'][0],
                   {'id': 'b', 'track': 'web', 'task_kind': 'body'}]}
        rows = [{**self.record, 'provider_metadata': {'binary_sha256': 'first-build'}},
                {**self.record, 'case_id': 'b',
                 'provider_metadata': {'binary_sha256': 'replacement-build'}}]
        result = require_reliable(dataset, rows)
        self.assertEqual(result['implementation_inventory']['p'],
                         {'first-build': 1, 'replacement-build': 1})
        rows[1]['provider_metadata']['binary_sha256'] = 'first-build'
        self.assertTrue(require_reliable(dataset, rows)['complete'])

    def test_separately_named_builds_can_be_compared_on_the_complete_population(self):
        dataset = {**self.dataset, 'coverage_plan': {'web': ['release', 'candidate']}}
        rows = [{**self.record, 'provider': name,
                 'provider_metadata': {'binary_sha256': name + '-build'}}
                for name in ('release', 'candidate')]
        self.assertTrue(require_reliable(dataset, rows)['complete'])

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
