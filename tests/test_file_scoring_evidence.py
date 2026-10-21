"""Saved file evidence must be complete before it can be rescored."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.artifacts import file_scoring_metadata


class FileScoringEvidenceTests(unittest.TestCase):
    def test_missing_delivery_is_not_malformed_evidence(self):
        self.assertEqual(file_scoring_metadata({'task_kind': 'file'}, {}, Path('.')), {})

    def test_malformed_saved_claims_fail_before_scoring(self):
        for evaluation in (None, [], 'invalid', {'files': {}}, {'files': [{}]},
                           {'files': [{'artifact_path': 'file'}]}):
            with self.subTest(evaluation=evaluation), self.assertRaises(ValueError):
                file_scoring_metadata({'task_kind': 'file'},
                                      {'file_evaluation': evaluation}, Path('.'))

    def test_rescore_preserves_each_delivered_file_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'retained delivered bytes'
            (root / 'file.bin').write_bytes(raw)
            binding = {'artifact_path': 'file.bin', 'sha256': hashlib.sha256(raw).hexdigest()}
            metadata = {'file_evaluation': {'files': [binding]}}
            with patch('lexbench_webfetch.file_acceptance.evaluate_files',
                       return_value={'files': [{'passed': False}]}):
                result = file_scoring_metadata({'task_kind': 'file'}, metadata, root)
            self.assertEqual(result['file_evaluation']['files'], [{**binding, 'passed': False}])
            self.assertEqual(metadata['file_evaluation']['files'], [binding])
            with patch('lexbench_webfetch.file_acceptance.evaluate_files',
                       return_value={'files': []}):
                with self.assertRaisesRegex(ValueError, 'file count'):
                    file_scoring_metadata({'task_kind': 'file'}, metadata, root)
