import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_dynamic_content import prepare
from harness.web_fetch.dynamic_reference import entry_facts
from harness.web_fetch.metrics import evaluate_content

ROOT = Path(__file__).resolve().parents[1]


class CaptureFreshnessTests(unittest.TestCase):
    def test_saved_capture_cannot_be_reused_for_live_run(self):
        captures = json.loads((ROOT/'research/dynamic-browser-captures.json').read_text())
        checks = json.loads((ROOT/'research/dynamic-completion-checks.json').read_text())
        for row in captures + checks:
            row['checked_at'] = '2000-01-01T00:00:00Z'
        with tempfile.TemporaryDirectory() as folder, patch('scripts.prepare_dynamic_content.requests.get') as get:
            output = Path(folder)/'new-run'
            with self.assertRaisesRegex(ValueError, 'stale'):
                prepare(captures, output, checks)
            self.assertFalse(output.exists())
            get.assert_not_called()

    def test_new_observed_values_replace_old_fact_values(self):
        # Rules derive from independent captures, not fixed corpus names.
        captures = json.loads((ROOT/'research/dynamic-expansion-captures.json').read_text())
        case = next(c for c in captures if c['id'] == 'dynamic-iframe')
        updated = copy.deepcopy(case['entries'])
        old = updated[0]
        updated[0] = 'Updated family name'
        facts = entry_facts(case['id'], updated)
        fresh = '\n'.join(updated)
        stale = '\n'.join(case['entries'])
        score = lambda text: evaluate_content(text, [], required_facts=facts, min_content_chars=1)
        self.assertTrue(score(fresh)['content_pass'])
        self.assertFalse(score(stale)['content_pass'])
        self.assertNotEqual(old, updated[0])
