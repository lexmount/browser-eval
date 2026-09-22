"""A failed optional capture can be retried without losing its output identity."""

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import capture_wcxb_optional_ui


@contextmanager
def fixture(_records):
    yield 'http://frozen.test', Path('/unused-ca.pem')


class OptionalUiCaptureTest(unittest.TestCase):
    def test_timeout_can_retry_same_output_path(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            dataset = root / 'dataset.json'
            dataset.write_text('{}')
            release = root / 'release'
            candidate = root / 'candidate'
            release.write_bytes(b'release binary')
            candidate.write_bytes(b'candidate binary')
            output = root / 'evidence'
            argv = ['capture_wcxb_optional_ui.py', '--dataset', str(dataset),
                    '--release', str(release), '--candidate', str(candidate),
                    '--output', str(output)]
            record = {'track_id': '4017', 'html': '<details>Product body</details>'}
            with (patch.object(sys, 'argv', argv),
                  patch.object(capture_wcxb_optional_ui, 'load_wcxb_source', return_value=([record], 'source-hash')),
                  patch.object(capture_wcxb_optional_ui, 'fixture_server', fixture),
                  patch.object(capture_wcxb_optional_ui.subprocess, 'run', side_effect=subprocess.TimeoutExpired('moli', 60))):
                with self.assertRaises(subprocess.TimeoutExpired):
                    capture_wcxb_optional_ui.main()
            self.assertFalse(output.exists())

            responses = [subprocess.CompletedProcess([], 0, b'Navigation only', b''),
                         subprocess.CompletedProcess([], 0, b'Struggling with discomfort in your knees', b'')]
            with (patch.object(sys, 'argv', argv),
                  patch.object(capture_wcxb_optional_ui, 'load_wcxb_source', return_value=([record], 'source-hash')),
                  patch.object(capture_wcxb_optional_ui, 'fixture_server', fixture),
                  patch.object(capture_wcxb_optional_ui.subprocess, 'run', side_effect=responses)):
                capture_wcxb_optional_ui.main()
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertFalse(receipt['builds']['release']['description_present'])
            self.assertTrue(receipt['builds']['candidate']['description_present'])
            self.assertEqual((output / 'candidate.md').read_bytes(), responses[1].stdout)


if __name__ == '__main__':
    unittest.main()
