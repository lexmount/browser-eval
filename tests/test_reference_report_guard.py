import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.report_build import build_report
from harness.web_fetch.reference_lifecycle import ReferenceLifecycleError
from scripts.rescore_content import rescore


class ReferenceReportGuardTests(unittest.TestCase):
    def fixture(self, root):
        case = {'id': 'c', 'name': 'c', 'url': 'https://example.com', 'category': 'test',
                'track': 'web', 'ground_truth_main_content': 'new answer'}
        record = {'attempt_id': 'p__c__r001', 'provider': 'p', 'case_id': 'c',
                  'artifact_path': 'artifacts/p/c/run.txt', 'metrics': {}, 'ok': True}
        (root/'dataset.json').write_text(json.dumps({'cases': [case]}))
        (root/'results.jsonl').write_text(json.dumps(record)+'\n')
        (root/'manifest.json').write_text(json.dumps({'attempt_ids': [record['attempt_id']],
            'reference_checks': {'status': 'awaiting_postcheck', 'cases': {
                'c': {'reference_sha256': 'a'*64}}}}))

    def test_report_blocks_pending_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            output = root/'report'
            with self.assertRaises(ReferenceLifecycleError):
                build_report(root, output)
            self.assertFalse(output.exists())

    def test_rescore_cannot_drop_record_hash_to_skip_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            before = (root/'results.jsonl').read_bytes()
            with self.assertRaises(ReferenceLifecycleError):
                rescore(root)
            self.assertEqual((root/'results.jsonl').read_bytes(), before)
