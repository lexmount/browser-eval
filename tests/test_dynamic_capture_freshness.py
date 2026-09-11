import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.prepare_dynamic_content import prepare
from harness.web_fetch.dynamic_reference import entry_facts
from harness.web_fetch.metrics import evaluate_content
from harness.web_fetch.reference_lifecycle import preflight_reference_checks
from harness.web_fetch.stable_annotations import DYNAMIC_FORBIDDEN_ANY, SNAPSHOT_METRIC_SCOPE

ROOT = Path(__file__).resolve().parents[1]


class CaptureFreshnessTests(unittest.TestCase):
    def captures(self):
        captures = json.loads((ROOT/'research/dynamic-browser-captures.json').read_text())
        checks = json.loads((ROOT/'research/dynamic-completion-checks.json').read_text())
        for row in captures + checks:
            row['checked_at'] = '2000-01-01T00:00:00Z'
        return captures, checks

    def response(self):
        return Mock(content=b'<main>Independent initial response</main>', status_code=200)

    def test_old_sample_can_prepare_stable_contract_without_freshness_claim(self):
        captures, checks = self.captures()
        before = copy.deepcopy((captures, checks))
        with tempfile.TemporaryDirectory() as folder, patch('scripts.prepare_dynamic_content.requests.get', return_value=self.response()) as get:
            output = Path(folder)/'new-run'
            prepare(captures, output, checks)
            dataset = json.loads((output/'dataset.json').read_text())
            self.assertEqual(get.call_count, len(captures))
            for case, capture in zip(dataset['cases'], captures):
                self.assertEqual(case['source_verification']['checked_at'], capture['checked_at'])
                self.assertEqual(case['source_benchmark']['snapshot_metric_scope'], SNAPSHOT_METRIC_SCOPE)
                self.assertIn('acceptance_contract', case)
                for token in DYNAMIC_FORBIDDEN_ANY.get(case['id'], ()):
                    self.assertIn(token, case['forbidden_any'])
                self.assertEqual((output/'sources'/case['id']/'rendered.txt').read_text(), capture['rendered_text'])
                evidence = json.loads((output/'sources'/case['id']/'evidence.json').read_text())
                self.assertEqual(evidence['metric_scope'], SNAPSHOT_METRIC_SCOPE)
                self.assertFalse(evidence['contemporaneous_pair_claimed'])
            self.assertIsNone(preflight_reference_checks(dataset['cases'], output/'dataset.json'))
            exact_case = copy.deepcopy(dataset['cases'][0])
            del exact_case['acceptance_contract']
            with self.assertRaisesRegex(ValueError, '30 minutes'):
                preflight_reference_checks([exact_case], output/'dataset.json')
        self.assertEqual((captures, checks), before)

    def test_invalid_timestamps_completion_and_source_integrity_fail_before_writes_or_http(self):
        mutations = (
            lambda captures, checks: captures[0].update(checked_at='2000-01-01'),
            lambda captures, checks: checks[0].update(checked_at='2999-01-01T00:00:00Z'),
            lambda captures, checks: checks[0].update(satisfied=False),
            lambda captures, checks: checks[0].update(elapsed_ms=checks[0]['timeout_ms'] + 1),
            lambda captures, checks: checks[0].update(elapsed_ms=float('nan')),
            lambda captures, checks: captures[0].update(reference_sha256='wrong'),
            lambda captures, checks: captures[0].update(rendered_sha256='wrong'),
            lambda captures, checks: captures[0].update(rendered_text=captures[0]['rendered_text'] + '\nUnrecorded source text'),
            lambda captures, checks: captures[0].update(selector=''),
            lambda captures, checks: checks.append(copy.deepcopy(checks[0])),
        )
        for mutate in mutations:
            captures, checks = self.captures()
            mutate(captures, checks)
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as folder, patch('scripts.prepare_dynamic_content.requests.get') as get:
                output = Path(folder)/'new-run'
                with self.assertRaises(ValueError):
                    prepare(captures, output, checks)
                self.assertFalse(output.exists())
                get.assert_not_called()

    def test_strict_exclusion_registry_is_explicit_and_fails_closed(self):
        captures, checks = self.captures()
        incomplete = dict(DYNAMIC_FORBIDDEN_ANY)
        del incomplete['dynamic-reviews-graphql']
        with tempfile.TemporaryDirectory() as folder, \
                patch('scripts.prepare_dynamic_content.DYNAMIC_FORBIDDEN_ANY', incomplete), \
                patch('scripts.prepare_dynamic_content.requests.get') as get:
            output = Path(folder)/'new-run'
            with self.assertRaisesRegex(ValueError, 'explicit per-case declaration'):
                prepare(captures, output, checks)
            self.assertFalse(output.exists())
            get.assert_not_called()

    def test_fixed_demo_content_changes_do_not_silently_rewrite_registry(self):
        captures, checks = self.captures()
        captures[0]['entries'][0] = captures[0]['entries'][0].replace('Albert Einstein', 'Unrelated Author')
        captures[0]['rendered_text'] = '\n\n'.join(captures[0]['entries'])
        checks[0]['entries'] = copy.deepcopy(captures[0]['entries'])
        with tempfile.TemporaryDirectory() as folder, patch('scripts.prepare_dynamic_content.requests.get') as get:
            output = Path(folder)/'new-run'
            with self.assertRaisesRegex(ValueError, 'reviewed acceptance contract'):
                prepare(captures, output, checks)
            self.assertFalse(output.exists())
            get.assert_not_called()

    def test_new_observed_values_replace_old_fact_values(self):
        # Snapshot diagnostics can describe a new independent sample, without
        # silently changing the separately reviewed stable acceptance contract.
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
