import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.score_wcxb_results import classify, official_evaluator, percentile
from scripts.select_wcxb_sample import choose, full_test_exclusions
from scripts.run_webmainbench_benchmark import PROVIDERS, WCXB_PROVIDERS
from harness.web_fetch.report import format_wcxb_conditional_latency


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
        profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
        self.assertEqual(tuple(profile['providers']), WCXB_PROVIDERS)
        self.assertIn('moli-1.1.7', PROVIDERS)
        self.assertNotIn('moli-1.1.7', WCXB_PROVIDERS)

    def test_frozen_manifest_is_unique_stratified_and_profile_bound(self):
        raw = (ROOT / 'evaluation/public-wcxb-test-full.json').read_bytes()
        sample = json.loads(raw)
        profile = json.loads((ROOT / 'evaluation/current-report.json').read_text())
        self.assertEqual(sample['size'], 511)
        self.assertEqual(len(sample['cases']), 511)
        self.assertEqual(len({r['file_id'] for r in sample['cases']}), 511)
        self.assertEqual(sum(sample['distribution']['sample'].values()), 511)
        self.assertEqual(set(sample['distribution']['sample']), set(sample['distribution']['parent']))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), profile['sample_manifest_sha256'])
        self.assertEqual(sample['source_sha256'], profile['source_dataset_sha256'])
        self.assertEqual(sample['primary_quality_case_count'], 504)

    def test_full_test_exclusions_follow_source_content_only(self):
        manifest = json.loads((ROOT / 'evaluation/public-wcxb-test-full.json').read_text())
        self.assertEqual({r['file_id'] for r in manifest['exclusions']},
                         {'4066', '4145', '4181', '4285', '4482', '4542', '4922'})
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


if __name__ == '__main__':
    unittest.main()
