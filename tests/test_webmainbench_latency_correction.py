"""A timing supersession must leave all scored evidence byte-equivalent."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.webmainbench_latency_correction import validate_latency_correction


class WebMainBenchLatencyCorrectionTests(unittest.TestCase):
    def test_complete_moli_scope_preserves_scores_and_other_methods(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = []
            new = []
            changes = []
            for case_index in range(200):
                for provider in ('moli', 'moli-1.1.7', 'kitesurf', 'lightpanda',
                                 'obscura', 'chromium'):
                    attempt_id = f'{provider}-{case_index}'
                    prior = {'attempt_id': attempt_id, 'provider': provider,
                             'elapsed_ms': 100.0, 'ok': True,
                             'output_sha256': 'same-output', 'metrics': {'score': 0.5},
                             'provider_metadata': {'binary_sha256': 'same-binary'}}
                    current = json.loads(json.dumps(prior))
                    if provider.startswith('moli'):
                        current['elapsed_ms'] = 90.0
                        current['provider_metadata']['latency_correction'] = {
                            'kind': 'certified_balanced_final_round',
                            'selected_round': 2, 'original_elapsed_ms': 100.0,
                            'corrected_elapsed_ms': 90.0}
                        changes.append({'attempt_id': attempt_id,
                                        'before_ms': 100.0, 'after_ms': 90.0})
                    old.append(prior)
                    new.append(current)
            prior_raw = ''.join(json.dumps(row) + '\n' for row in old).encode()
            current_raw = ''.join(json.dumps(row) + '\n' for row in new).encode()
            (root / 'prior-latency-results.jsonl').write_bytes(prior_raw)
            (root / 'results.jsonl').write_bytes(current_raw)
            receipt = {'schema_version': 1,
                       'kind': 'webmainbench_moli_200_balanced_latency_only_v1',
                       'selected_round': 2, 'changed_result_count': 400,
                       'prior_results_sha256': hashlib.sha256(prior_raw).hexdigest(),
                       'output_results_sha256': hashlib.sha256(current_raw).hexdigest(),
                       'changes': changes}
            (root / 'latency-corrections.json').write_text(json.dumps(receipt))
            self.assertEqual(validate_latency_correction(root), receipt)
            new[0]['metrics']['score'] = 1.0
            tampered = ''.join(json.dumps(row) + '\n' for row in new).encode()
            (root / 'results.jsonl').write_bytes(tampered)
            receipt['output_results_sha256'] = hashlib.sha256(tampered).hexdigest()
            (root / 'latency-corrections.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'scored evidence'):
                validate_latency_correction(root)


if __name__ == '__main__':
    unittest.main()
