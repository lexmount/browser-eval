import os
from pathlib import Path
import subprocess
import sys
import unittest


class ReportPublicTargetTests(unittest.TestCase):
    def test_explicit_target_controls_export_evidence_links(self):
        env = dict(os.environ, WEBFETCH_REPORT_PUBLIC_URL='https://example.com/new-report/')
        result = subprocess.run([sys.executable, '-c',
            'from harness.web_fetch.report_explainer import evidence_url; '
            'print(evidence_url("guide/index.html")); print(evidence_url("results.jsonl"))'],
            env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), [
            'https://example.com/new-report#page=guide%2Findex.html',
            'https://example.com/new-report#page=guide%2Findex.html&anchor=evidence'])
