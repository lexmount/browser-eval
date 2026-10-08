import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.score_wcxb_results import classify, official_evaluator, percentile
from harness.web_fetch.wcxb_dataset import choose, full_test_exclusions, validate_manifest
from harness.web_fetch.webmainbench_runner import PROVIDERS, WCXB_PROVIDERS
from harness.web_fetch.report import format_wcxb_conditional_latency
from harness.web_fetch.wcxb_latency import load_full_paired_latency, summarize_full_paired_latency


ROOT = Path(__file__).resolve().parents[1]


class WCXBContractTests(unittest.TestCase):
    def test_changed_official_evaluator_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'evaluate.py').write_text('raise RuntimeError("executed")\n')
            with self.assertRaisesRegex(ValueError, 'official evaluator hash differs'):
                official_evaluator(Path(folder), '0' * 64)

    def test_conditional_latency_is_unavailable_without_delivered_pages(self):
        self.assertEqual(format_wcxb_conditional_latency(None), '—')
        self.assertEqual(format_wcxb_conditional_latency(1234.2), '1,234 ms')

    def test_wcxb_participants_do_not_inherit_webmainbench_version_rows(self):
        profile = json.loads((ROOT / 'evaluation/wcxb/current-report.json').read_text())
        self.assertEqual(tuple(profile['providers']), WCXB_PROVIDERS)
        self.assertIn('moli-1.1.7', PROVIDERS)
        self.assertNotIn('moli-1.1.7', WCXB_PROVIDERS)

    def test_frozen_manifest_is_unique_stratified_and_profile_bound(self):
        raw = (ROOT / 'evaluation/public-wcxb-test-qualified.json').read_bytes()
        sample = json.loads(raw)
        profile = json.loads((ROOT / 'evaluation/wcxb/current-report.json').read_text())
        self.assertEqual(sample['size'], 504)
        self.assertEqual(len(sample['cases']), 504)
        self.assertEqual(len({r['file_id'] for r in sample['cases']}), 504)
        self.assertEqual(sum(sample['distribution']['sample'].values()), 504)
        self.assertEqual(set(sample['distribution']['sample']), set(sample['distribution']['parent']))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), profile['sample_manifest_sha256'])
        self.assertEqual(sample['source_sha256'], profile['source_dataset_sha256'])
        self.assertEqual(sample['primary_quality_case_count'], 504)

    def test_full_test_exclusions_follow_source_content_only(self):
        manifest = json.loads((ROOT / 'evaluation/public-wcxb-test-qualified.json').read_text())
        self.assertEqual(manifest['scope'], 'qualified_test')
        self.assertNotIn('exclusions', manifest)
        records = [
            {'track_id': 'a', 'html': '<html><body><p>Visible</p></body></html>',
             'groundtruth_content': '', 'meta': {'page_type': 'article'}},
            {'track_id': 'b', 'html': '<html><head><title>Only title</title></head><body><script>hidden</script></body></html>',
             'groundtruth_content': 'Expected article', 'meta': {'page_type': 'article'}},
            {'track_id': 'c', 'html': '<html><body><p>Expected article</p></body></html>',
             'groundtruth_content': 'Expected article', 'meta': {'page_type': 'article'}},
        ]
        self.assertEqual([(r['file_id'], r['reason']) for r in full_test_exclusions(records)],
                         [('a', 'empty_main_content_reference'),
                          ('b', 'reference_body_absent_from_frozen_html')])
        sample = {**manifest, 'source_sha256': 'source', 'distribution': {'parent': {'article': 3}, 'sample': {'article': 1}},
                  'size': 1, 'cases': [{'file_id': 'c', 'page_type': 'article'}], 'primary_quality_case_count': 1}
        selected, _ = validate_manifest(records, 'source', sample)
        self.assertEqual([row['track_id'] for row in selected], ['c'])

    def test_selection_uses_type_and_id_only(self):
        records = [
            {'track_id': str(i), 'meta': {'page_type': 'article' if i < 7 else 'forum'},
             'provider_output': 'would be ignored'}
            for i in range(10)
        ]
        first, distribution = choose(records, 5)
        for row in records:
            row['provider_output'] = 'changed after selection'
        second, _ = choose(records, 5)
        self.assertEqual([r['track_id'] for r in first], [r['track_id'] for r in second])
        self.assertEqual(distribution['sample'], {'article': 4, 'forum': 1})

    def test_diagnostic_labels_do_not_relabel_empty_references_as_browser_failure(self):
        ok = {'ok': True}
        failed = {'ok': False}
        self.assertEqual(classify(ok, 'returned text', 0, 0, ''), 'empty_reference')
        self.assertEqual(classify(failed, '', 0, 0, 'reference'), 'request_error')
        self.assertEqual(classify(ok, 'returned text', .2, .8, 'reference'), 'low_precision')

    def test_latency_percentiles_include_all_calls(self):
        self.assertEqual(percentile([10, 20, 30], .5), 20)
        self.assertAlmostEqual(percentile([10, 20, 30], .95), 29)

    def test_paired_latency_requires_complete_hash_bound_calls(self):
        cases = {str(i) for i in range(4)}
        rows = []
        for round_number in (0, 1):
            for index, case in enumerate(sorted(cases)):
                first = 'release' if (index + round_number) % 2 == 0 else 'pr'
                for order, method in enumerate((first, 'pr' if first == 'release' else 'release'), 1):
                    rows.append({'case': case, 'round': round_number, 'method': method,
                                 'order': order, 'elapsed_ms': 10 if method == 'release' else 12,
                                 'ok': True, 'error_type': None, 'output_sha256': 'a' * 64})
        profile = {'source_dataset_sha256': 'source', 'sample_manifest_sha256': 'manifest',
                   'release_binary_sha256': 'release', 'candidate_binary_sha256': 'candidate'}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def write(calls, binary='candidate'):
                raw = b''.join((json.dumps(row) + '\n').encode() for row in calls)
                (root / 'calls.jsonl').write_bytes(raw)
                receipt = {'kind': 'wcxb_full_paired_latency', 'case_count': len(cases),
                           'rounds': 2, 'selection': 'final round, all cases',
                           'call_count': len(cases) * 4, 'source_sha256': 'source',
                           'manifest_sha256': 'manifest',
                           'binary_sha256': {'release': 'release', 'pr': binary},
                           'calls_sha256': hashlib.sha256(raw).hexdigest(),
                           'host_load_1m_checks': [1.0] * 50, 'quiet_host_load_limit': 4.0}
                receipt_raw = json.dumps(receipt).encode()
                (root / 'receipt.json').write_bytes(receipt_raw)
                profile['latency_correction_receipt_sha256'] = hashlib.sha256(receipt_raw).hexdigest()

            write(rows)
            _, selected = load_full_paired_latency(root, profile, cases)
            self.assertEqual(summarize_full_paired_latency(selected, cases)['paired_delta_p50_ms'], 2)
            write(rows[:-1])
            with self.assertRaisesRegex(ValueError, 'inventory is incomplete'):
                load_full_paired_latency(root, profile, cases)
            write(rows, binary='different')
            with self.assertRaisesRegex(ValueError, 'protocol or identity differs'):
                load_full_paired_latency(root, profile, cases)
            changed = [dict(row) for row in rows]
            changed[-1]['output_sha256'] = 'b' * 64
            write(changed)
            with self.assertRaisesRegex(ValueError, 'output changed between rounds'):
                load_full_paired_latency(root, profile, cases)


if __name__ == '__main__':
    unittest.main()
