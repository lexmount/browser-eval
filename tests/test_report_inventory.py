import copy
import hashlib
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class ReportInventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((ROOT/'configs/evaluation-inventory.json').read_text())
        digest = hashlib.sha256(b'fixture').hexdigest()
        self.dataset = {
            'cases': [{'id': cid, 'track': track, 'ground_truth_main_content': 'fixture',
                       'source_verification': {'reference_sha256': digest}}
                      for track, group in self.inventory.items() for cid in group['case_ids']],
            'coverage_plan': {track: group['providers'] for track, group in self.inventory.items()},
        }
        self.records = [
            {'provider': provider, 'case_id': cid, 'attempt_id': f'{provider}__{cid}',
             'artifact_path': f'artifacts/{provider}/{cid}.txt', 'ok': True,
             'metrics': {'content_sha256': digest}}
            for group in self.inventory.values() for cid in group['case_ids']
            for provider in group['providers']
        ]

    def check(self):
        # Exercise the actual script with in-memory report files, no generated
        # report rebuild, canonical data writes or environment/config loading.
        root = Path('/__synthetic_inventory_report__')
        files = {
            root/'dataset.json': json.dumps(self.dataset),
            root/'results.jsonl': '\n'.join(json.dumps(r) for r in self.records),
            root/'execution.json': json.dumps({'batches': [{'attempt_ids': [r['attempt_id'] for r in self.records]}]}),
            ROOT/'configs/evaluation-inventory.json': json.dumps(self.inventory),
        }
        read_text, read_bytes, rglob = Path.read_text, Path.read_bytes, Path.rglob

        def text(path, *args, **kwargs):
            return files[path] if path in files else read_text(path, *args, **kwargs)

        def binary(path, *args, **kwargs):
            return b'fixture' if path.is_relative_to(root) else read_bytes(path, *args, **kwargs)

        def glob(path, *args, **kwargs):
            return iter(()) if path == root else rglob(path, *args, **kwargs)

        with patch.object(Path, 'read_text', text), patch.object(Path, 'read_bytes', binary), \
                patch.object(Path, 'rglob', glob), patch('sys.argv', ['check_report.py', str(root)]):
            runpy.run_path(str(ROOT/'scripts/check_report.py'), run_name='__main__')

    def test_reviewed_inventory_passes(self):
        self.check()

    def test_duplicate_dataset_case_rejected(self):
        self.dataset['cases'].append(copy.deepcopy(self.dataset['cases'][-1]))
        with self.assertRaisesRegex(AssertionError, 'Duplicate dataset case'):
            self.check()

    def test_duplicate_inventory_case_rejected(self):
        self.inventory['dynamic']['case_ids'].append(self.inventory['dynamic']['case_ids'][0])
        with self.assertRaisesRegex(AssertionError, 'Duplicate inventory case'):
            self.check()

    def test_deleted_case_and_records_rejected(self):
        cid = self.inventory['dynamic']['case_ids'][-1]
        self.dataset['cases'] = [c for c in self.dataset['cases'] if c['id'] != cid]
        self.records = [r for r in self.records if r['case_id'] != cid]
        with self.assertRaisesRegex(AssertionError, 'reviewed independent inventory'):
            self.check()


if __name__ == '__main__':
    unittest.main()
