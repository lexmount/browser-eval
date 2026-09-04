import unittest

from scripts.summarize_headless import paired_latency_row


class HeadlessSummaryTests(unittest.TestCase):
    def test_no_common_success_has_unavailable_latency(self):
        self.assertEqual(paired_latency_row({
            'provider': 'moli', 'both_pass_ids': [],
            'provider_p50_ms': None, 'chromium_p50_ms': None,
        }), '| moli | 0 | — | — |')

    def test_measured_zero_is_not_unavailable(self):
        self.assertEqual(paired_latency_row({
            'provider': 'moli', 'both_pass_ids': ['case'],
            'provider_p50_ms': 0, 'chromium_p50_ms': 12.345,
        }), '| moli | 1 | 0.00 | 12.35 |')
