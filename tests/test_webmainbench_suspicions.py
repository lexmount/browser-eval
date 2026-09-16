"""Regression checks for cross-layer public-benchmark anomaly signals."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.audit_webmainbench_suspicions import scan
from harness.web_fetch.report import _wmb_error_page
from harness.web_fetch.webmainbench_diagnostics import ERROR_PAGE, require_valid_teds_for_release


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class WebMainBenchSuspicionTests(unittest.TestCase):
    def test_release_rejects_impossible_teds_but_allows_valid_zero(self):
        def record(distance):
            return {'metrics': {'table_TEDS': {'score': 0, 'details': {
                'predicted_nodes': 5, 'groundtruth_nodes': 570, 'edit_distance': distance}}}}
        with self.assertRaisesRegex(ValueError, '1 impossible TEDS'):
            require_valid_teds_for_release([record(.9)])
        require_valid_teds_for_release([record(570)])
        require_valid_teds_for_release([{'metrics': {}}])

    def test_report_and_audit_share_error_page_detection(self):
        for text, expected in [("This site can’t be reached\nexample.test", True),
                               ("# 无法访问此网站\nERR_ACCESS_DENIED", True),
                               ("An article explaining why This site can't be reached appears", False),
                               ("# A normal article\nUseful body", False)]:
            self.assertEqual(_wmb_error_page(text), expected)
            self.assertEqual(bool(ERROR_PAGE.search(text)), expected)

    def test_flags_impossible_tree_distance_and_error_page_without_a_provider_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            (evidence / "artifacts").mkdir(parents=True)
            source = root / "source.jsonl"
            html = '<script>window.location.search += "&nocdn=true"</script>'
            reference = "| A | B |\n|---|---|\n| 1 | 2 |"
            case = {"track_id": "case-1", "html": html, "groundtruth_content": reference}
            source.write_text(json.dumps(case) + "\n", encoding="utf-8")
            dataset = {"cases": [{"id": "case-1", "input_html_sha256": sha(html),
                                   "reference_sha256": sha(reference)}]}
            (evidence / "dataset.json").write_text(json.dumps(dataset), encoding="utf-8")
            output = "# Error response\nError code: 404"
            (evidence / "artifacts" / "out.md").write_text(output, encoding="utf-8")
            record = {"attempt_id": "method__case-1__r001", "provider": "method", "case_id": "case-1",
                      "artifact_path": "artifacts/out.md", "ok": True, "provider_metadata": {},
                      "metrics": {"table_TEDS": {"success": True, "details": {
                          "predicted_nodes": 5, "groundtruth_nodes": 570, "edit_distance": 0.9}}}}
            (evidence / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            (evidence / "physical-attempts.jsonl").write_text(
                json.dumps({"provider": "method", "track_id": "case-1"}) + "\n", encoding="utf-8")
            result = scan(evidence, source)
            signals = set(result["results"][0]["signals"])
            self.assertEqual(result["flagged_results"], 1)
            self.assertIn("teds-distance-below-node-count-lower-bound", signals)
            self.assertIn("error-page-counted-as-output", signals)
            self.assertIn("fixture-query-navigation-candidate", signals)
            self.assertEqual(result["kind"], "investigation_signals_not_verdicts")


if __name__ == "__main__":
    unittest.main()
