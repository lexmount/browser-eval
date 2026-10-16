"""Invariant questions do not depend on old live values; evidence stays bound."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.reference_lifecycle import preflight_reference_checks
from harness.web_fetch.runner import run_evaluation, rescore_evaluation
from lexbench_webfetch.metrics import task_passed


class StableLifecycleTests(unittest.TestCase):
    def case(self):
        return EvalCase(id='catalog', name='Catalog', category='dynamic', expected_any=[],
            url='https://example.test/catalog', track='dynamic',
            ground_truth_main_content='Old item\n$10',
            source_verification={'state': 'valid', 'checked_at': '2020-01-01T00:00:00Z'},
            acceptance_contract={'version': 1, 'schema': 'product', 'minimum_records': 1,
                'stable_text_anchors': [{'id': 'section', 'alternatives': ['Catalog']}]})

    def test_old_capture_does_not_expire_stable_question(self):
        case = self.case()
        self.assertIsNone(preflight_reference_checks([case], Path('/unused/dataset.json'),
            now=datetime(2030, 1, 1, tzinfo=timezone.utc)))

    def test_invalid_contract_cannot_skip_reference_validation(self):
        for contract in ({'schema': 'product'}, {'version': 1, 'schema': 'product', 'minimum_records': 0}):
            with self.assertRaises(ValueError):
                preflight_reference_checks([replace(self.case(), acceptance_contract=contract)],
                                           Path('/unused/dataset.json'))

    def test_runner_scores_new_values_and_binds_rule_not_old_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = self.case()
            dataset = {'id': 'stable', 'tool': 'web_fetch', 'cases': [deepcopy(case.__dict__)]}
            path = root/'dataset.json'
            path.write_text(json.dumps(dataset))
            provider = Mock()
            provider.name, provider.type, provider.min_interval_seconds = 'p', 'synthetic', 0
            provider.validate.return_value = []
            provider.fetch.return_value = ProviderResult(ok=True, content='# Catalog\n\nNew item\n$99')
            run = root/'run'
            run_evaluation(output_dir=run, dataset_meta=dataset, dataset_path=path,
                cases=[case], providers=[provider], provider_config_path=root/'providers.json',
                runs=1, timeout=10, delay=0)
            row = json.loads((run/'results.jsonl').read_text())
            self.assertTrue(task_passed(row['ok'], row['metrics']))
            self.assertEqual(row['metrics']['acceptance_contract_sha256'], hashlib.sha256(
                json.dumps(case.acceptance_contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
            self.assertNotIn('reference_checks', json.loads((run/'manifest.json').read_text()))
            raw = (run/row['artifact_path']).read_bytes()
            rescored, _ = rescore_evaluation(run_dir=run, dataset_meta=dataset, dataset_path=path, cases=[case])
            self.assertTrue(task_passed(rescored[0]['ok'], rescored[0]['metrics']))
            self.assertEqual((run/row['artifact_path']).read_bytes(), raw)
            (run/row['artifact_path']).write_text('Tampered')
            with self.assertRaisesRegex(ValueError, 'integrity|Public call log output binding changed|Artifact differs from its result binding'):
                rescore_evaluation(run_dir=run, dataset_meta=dataset, dataset_path=path, cases=[case])


if __name__ == '__main__':
    unittest.main()
