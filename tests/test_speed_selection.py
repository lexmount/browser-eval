import hashlib
import json
import sys
import unittest

import pytest

from harness.web_fetch.reference_inputs import import_inputs
from harness.web_fetch.speed_cohort import select_cases, selection_main


@pytest.mark.parametrize('count', [0, -5, 3, True, 5.0])
def test_speed_selection_requires_a_positive_whole_five_case_scope(count):
    with pytest.raises(ValueError, match='feasible multiple of five'):
        select_cases([], count)


def test_speed_selection_uses_external_inputs_and_copies_only_selected_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv('BROWSER_EVAL_DATA_HOME', str(tmp_path / 'data-home'))
    dataset = tmp_path / 'checkout/dataset.json'
    dataset.parent.mkdir()
    cases = [{'id': f'case-{index}', 'url': f'https://h{index}.example/',
              'ground_truth_main_content': 'Independent text ' * (index + 1)}
             for index in range(10)]
    dataset.write_text(json.dumps({'cases': cases}))
    approved = tmp_path / 'approved'
    trees = {'sources': {}, 'list-references': {}}
    for case in cases:
        for tree, relative, raw in (
            ('sources', case['id'] + '/reference.txt', case['id'].encode()),
            ('list-references', case['id'] + '.json', b'{}'),
        ):
            path = approved / tree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            trees[tree][relative] = hashlib.sha256(raw).hexdigest()
    manifest = dataset.parent / 'source-inputs.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'kind': 'frozen_reference_inputs',
        'id': 'speed-fixture', 'dataset': dataset.name,
        'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(), 'trees': trees}))
    import_inputs(manifest, approved)
    output = tmp_path / 'selected'
    monkeypatch.setattr(sys, 'argv', ['select-speed', '--dataset', str(dataset),
                                    '--output', str(output), '--count', '5'])
    selection_main()
    selected = {case['id'] for case in json.loads((output / 'dataset.json').read_text())['cases']}
    assert len(selected) == 5
    assert {path.name for path in (output / 'sources').iterdir()} == selected
    assert {path.stem for path in (output / 'list-references').iterdir()} == selected
    for identifier in selected:
        assert (output / 'sources' / identifier / 'reference.txt').read_text() == identifier
    assert not (dataset.parent / 'sources').exists()


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
