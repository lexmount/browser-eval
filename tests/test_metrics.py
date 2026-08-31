import unittest

from browser_harness_eval.metrics import evaluate_content, summarize


class MetricsTest(unittest.TestCase):
    def test_content_metrics_are_deterministic(self) -> None:
        result = evaluate_content("# Title\n[Link](https://example.com) Alpha", ["Alpha", "Beta"])
        self.assertTrue(result["content_pass"])
        self.assertEqual(result["marker_hits"], 1)
        self.assertEqual(result["marker_recall"], 0.5)
        self.assertEqual(result["markdown_headings"], 1)
        self.assertEqual(result["markdown_links"], 1)

    def test_summary_separates_transport_and_content(self) -> None:
        base = {
            "provider": "p",
            "provider_type": "fixture",
            "case_id": "c",
            "case_name": "Case",
            "category": "static",
            "elapsed_ms": 100,
            "error_type": None,
        }
        records = [
            {**base, "ok": True, "metrics": {"content_pass": True, "marker_recall": 1.0, "content_chars": 10}},
            {**base, "ok": True, "metrics": {"content_pass": False, "marker_recall": 0.0, "content_chars": 5}},
        ]
        item = summarize(records)["providers"]["p"]
        self.assertEqual(item["transport_success_rate"], 1.0)
        self.assertEqual(item["content_pass_rate"], 0.5)
        self.assertEqual(item["failure_types"], {"content_validation_failure": 1})


if __name__ == "__main__":
    unittest.main()
