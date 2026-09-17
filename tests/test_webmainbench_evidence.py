import unittest

from harness.web_fetch.webmainbench_evidence import retry_chain_matches


class RetryBindingTests(unittest.TestCase):
    def test_final_call_and_accumulated_cost_are_independently_bound(self):
        attempts = [
            dict(physical_attempt=1, elapsed_ms=10.0, output_sha256="failed"),
            dict(physical_attempt=2, elapsed_ms=20.0, output_sha256="final"),
        ]
        row = dict(physical_attempt_count=2, elapsed_ms=20.0,
                   total_elapsed_ms=30.0, output_sha256="final")
        self.assertTrue(retry_chain_matches(row, attempts))
        for change in (
            {"elapsed_ms": 30.0}, {"total_elapsed_ms": 20.0},
            {"elapsed_ms": 20.0 + 1e-10}, {"total_elapsed_ms": 30.0 + 1e-10},
            {"output_sha256": "failed"}, {"physical_attempt_count": 1},
            {"elapsed_ms": float("nan")},
        ):
            with self.subTest(change=change):
                self.assertFalse(retry_chain_matches({**row, **change}, attempts))
        self.assertFalse(retry_chain_matches(row, []))
        self.assertFalse(retry_chain_matches(row, list(reversed(attempts))))
