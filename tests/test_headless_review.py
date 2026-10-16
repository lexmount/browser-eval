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
    def test_navigation_transport_failures_keep_specific_error_types(self):
        from types import SimpleNamespace

        cases = [
            ('Page.goto: net::ERR_CONNECTION_CLOSED at https://example.com/', 'network'),
            ('Page.goto: net::ERR_NAME_NOT_RESOLVED at https://example.com/', 'dns'),
            ('Page.goto: net::ERR_CONNECTION_TIMED_OUT at https://example.com/', 'timeout'),
            ('Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://example.com/', 'browser_error'),
            ('Page.goto: net::ERR_BLOCKED_BY_CLIENT at https://example.com/', 'browser_error'),
            ('Page.evaluate: net::ERR_CONNECTION_CLOSED', 'browser_error'),
        ]
        for message, expected in cases:
            with self.subTest(message=message):
                page = SimpleNamespace(goto=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError(message)))
                context = SimpleNamespace(new_page=lambda: page)
                browser = SimpleNamespace(version='fixture', new_context=lambda **kw: context, close=lambda: None)
                class Driver:
                    def __enter__(self):
                        return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser))
                    def __exit__(self, *args):
                        return False
                with patch.dict(sys.modules, {'playwright.sync_api': SimpleNamespace(sync_playwright=Driver)}):
                    result = fetch({'engine': 'chromium', 'binary': '/fixture',
                                    'url': 'https://example.com/', 'timeout': 1})
                self.assertFalse(result['ok'])
                self.assertEqual(result['error_type'], expected)
                self.assertIn(message, result['error_message'])

    def test_postcheck_uses_case_track_coverage(self):
        dataset = {'coverage_plan': {'web': ['web-p'], 'resources': ['resource-p']}}
        self.assertEqual(expected_providers(dataset, {'track': 'resources'}), {'resource-p'})
        self.assertEqual(expected_providers(dataset, {}), {'web-p'})

    def test_resume_rejects_observations_outside_exact_reference_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'observations.json'
            path.write_text(json.dumps([{'case_id':'exact'},{'case_id':'unavailable'}]))
            with self.assertRaises(ValueError):
                load_resumable_observations(path,{'exact':{}})

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
