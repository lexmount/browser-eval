import unittest

from scripts.select_headless_speed_cases import select_cases


class SpeedSelectionTests(unittest.TestCase):
    def test_selection_is_host_diverse_and_provider_blind(self):
        cases=[]
        for index in range(30):
            cases.append({'id':f'case-{index:02d}','url':f'https://h{index % 25}.example/page',
                          'track':'web','task_kind':'body',
                          'ground_truth_main_content':'x'*(index+1)})
        selected=select_cases(cases,20)
        self.assertEqual(len(selected),20)
        self.assertEqual(len({case['url'].split('/')[2] for case in selected}),20)
        self.assertEqual(selected,select_cases(list(reversed(cases)),20))

    def test_selection_fails_when_hosts_are_insufficient(self):
        cases=[{'id':str(i),'url':'https://same.example/'+str(i),'track':'web',
                'task_kind':'body','ground_truth_main_content':'x'*(i+1)} for i in range(20)]
        with self.assertRaisesRegex(ValueError,'distinct hosts'):
            select_cases(cases,20)


if __name__ == '__main__':
    unittest.main()
