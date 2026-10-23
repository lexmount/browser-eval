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

    def test_inventory_counts_cloud_unknown_and_retained_build_results(self):
        from harness.web_fetch.reliability import implementation_inventory
        rows = [
            {'provider': 'cloud', 'provider_metadata': {}},
            {'provider': 'cloud', 'provider_metadata': {'reported_version': 'service-v2'}},
            {'provider': 'local', 'provider_metadata': {'binary_sha256': 'build-a'}},
            {'provider': 'local', 'provider_metadata': None},
        ]
        inventory = implementation_inventory(rows)
        self.assertEqual(inventory['cloud'], {'unknown': 1, 'reported-version:service-v2': 1})
        self.assertEqual(inventory['local'], {'build-a': 1, 'unknown': 1})
        self.assertEqual(sum(sum(counts.values()) for counts in inventory.values()), len(rows))

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
