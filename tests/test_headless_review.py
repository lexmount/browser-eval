import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.record_io import read_completed_records
from harness.web_fetch.providers.headless_worker import fetch
from harness.web_fetch.reference_freeze import (
    load_resumable_observations,
)
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.reference_lifecycle import expected_providers


class HeadlessReviewTests(unittest.TestCase):
    def test_postcheck_uses_case_track_coverage(self):
        dataset = {'coverage_plan': {'web': ['web-p'], 'resources': ['resource-p']}}
        self.assertEqual(expected_providers(dataset, {'track': 'resources'}), {'resource-p'})
        self.assertEqual(expected_providers(dataset, {}), {'web-p'})

    def test_resume_discards_observations_outside_exact_reference_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'observations.json'
            path.write_text(json.dumps([{'case_id':'exact'},{'case_id':'unavailable'}]))
            self.assertEqual(set(load_resumable_observations(path,{'exact':{}})),{'exact'})

    def test_worker_rejects_unsupported_url_before_browser_launch(self):
        for url in ('file:///etc/passwd','javascript:alert(1)','https://example.com/\n'):
            with self.subTest(url=url), patch('harness.web_fetch.providers.headless_worker.subprocess.run') as run:
                with self.assertRaises(ValueError):
                    fetch({'url':url})
                run.assert_not_called()

    def test_partial_tail_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'results.jsonl'
            path.write_text('{"case_id":"a"}\n{"case_')
            self.assertEqual(read_completed_records(path),[{'case_id':'a'}])
            path.write_text('{"case_id":"a"}')
            self.assertEqual(read_completed_records(path),[{'case_id':'a'}])
            for content in ('bad\n{"case_id":"a"}\n','{"case_id":"a"}\nbad\n'):
                path.write_text(content)
                with self.assertRaises(json.JSONDecodeError):
                    read_completed_records(path)

    def test_optimized_python_rejects_invalid_inventory(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            try:
                dataset=json.loads(read_dataset_bytes(root/'evaluation/dataset.json'))
            except FileNotFoundError:
                self.skipTest('requires externally installed historical reference inputs')
            dataset['cases']=dataset['cases'][:1]
            (path/'dataset.json').write_text(json.dumps(dataset))
            for flags in ([],['-O']):
                result=subprocess.run([sys.executable,*flags,str(root/'scripts/check_headless_evidence.py'),str(path)],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Task inventory differs',result.stderr)
                self.assertNotIn('PASS:',result.stdout)


if __name__=='__main__':
    unittest.main()
