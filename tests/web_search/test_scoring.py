"""Equivalent pages count as hits; superficially similar URLs do not."""

import json
import unittest
from pathlib import Path

from harness.web_search.core import classify, evaluate, normalize
from harness.web_search.scoring import apply_identity_correction


class IdentityScenarios(unittest.TestCase):
    def test_verified_equivalent_url_hits(self):
        case = {
            "family": "web",
            "anchors": ["https://docs.test/page.html"],
            "identity_aliases": {"https://docs.test/page": "https://docs.test/page.html"},
        }
        results = normalize({"results": [{"url": "https://docs.test/page"}]})
        metrics = evaluate(case, results)
        row = dict(results=results, metrics=metrics, http_status=200, error="")
        self.assertEqual(classify(case, row), ("pass", "target_hit"))
        for url in ("https://docs.test/page?id=other", "https://www.docs.test/page"):
            with self.subTest(url=url):
                changed = normalize({"results": [{"url": url}]})
                row.update(results=changed, metrics=evaluate(case, changed))
                self.assertEqual(classify(case, row), ("fail", "target_miss"))

    def test_correction_preserves_calls_and_rejects_unproven_equivalence(self):
        root = Path(__file__).resolve().parents[2]
        profile = json.loads((root / "evaluation/web-search/current-report.json").read_text())
        correction = json.loads(
            (root / "evaluation/web-search/identity-correction.json").read_text()
        )
        case = next(c for c in profile["cases"] if c["id"] == "cn-07")
        case["anchors"] = [case["anchors"][0]]
        results = normalize({"results": [{"url": next(iter(correction["aliases"]["cn-07"]))}]})
        row = dict(
            id="control",
            case_id="cn-07",
            results=results,
            metrics=evaluate(case, results),
            elapsed_ms=123,
            cost=0.005,
        )
        revised, rows, changes = apply_identity_correction(profile, [row], correction)
        self.assertEqual(row["metrics"]["anchor_hit_10"], 0)
        self.assertEqual(rows[0]["metrics"]["anchor_hit_10"], 1)
        self.assertEqual(rows[0]["elapsed_ms"], 123)
        self.assertEqual(rows[0]["cost"], 0.005)
        self.assertEqual(len(changes), 1)
        self.assertNotIn("identity_aliases", case)
        wrong = "https://sre.google/resources/book-update/handling-overload/"
        target = "https://sre.google/sre-book/handling-overload/"
        correction["aliases"]["public-ki07"] = {wrong: target}
        with self.assertRaisesRegex(ValueError, "Unproven"):
            apply_identity_correction(profile, [row], correction)
