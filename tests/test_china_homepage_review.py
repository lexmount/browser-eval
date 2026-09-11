import hashlib
import json
import unittest
from pathlib import Path

from harness.web_fetch.stable_tasks import validate_acceptance_contract

ROOT = Path(__file__).resolve().parents[1]


class ChinaHomepageReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidates = json.loads(
            (ROOT / "research/china-homepage-candidates-2026-09-11.json").read_text()
        )
        cls.selection = json.loads((ROOT / "research/china-homepage-selection.json").read_text())
        cls.rules = json.loads((ROOT / "research/china-homepage-rules.json").read_text())

    def test_strict_unique_site_count_reconciliation(self):
        counts = self.selection["count_reconciliation"]
        self.assertEqual(counts["homepage_case_count"], 52)
        self.assertEqual(counts["legacy_popular_case_count"], 10)
        self.assertEqual(counts["definite_overlapping_site_domains"], ["amazon.com", "reddit.com"])
        self.assertEqual(counts["unique_existing_site_domain_count"], 60)
        self.assertEqual(counts["new_unique_site_domains_required"], 40)
        self.assertEqual(
            counts["unique_existing_site_domain_count"] + self.selection["selected_count"], 100
        )

    def test_selection_exactly_partitions_observed_candidates(self):
        candidates = {item["domain"] for item in self.candidates["candidates"]}
        selected = {item["domain"] for item in self.selection["cases"]}
        ineligible = {item["domain"] for item in self.selection["ineligible"]}
        reserves = {item["domain"] for item in self.selection["reserves"]}
        self.assertEqual(len(candidates), 76)
        self.assertEqual(len(selected), 40)
        self.assertEqual(len(ineligible), 24)
        self.assertEqual(len(reserves), 12)
        self.assertFalse(selected & ineligible)
        self.assertFalse(selected & reserves)
        self.assertFalse(ineligible & reserves)
        self.assertEqual(selected | ineligible | reserves, candidates)
        self.assertNotIn("360.cn", reserves)
        self.assertIn("360.cn", ineligible)
        self.assertEqual(max(item["china_rank"] for item in self.selection["cases"]), 82)

    def test_rules_cover_selection_and_remain_at_human_review_gate(self):
        selected = {item["id"]: item for item in self.selection["cases"]}
        rules = {item["id"]: item for item in self.rules["cases"]}
        self.assertEqual(set(rules), set(selected))
        self.assertEqual(self.rules["status"], "draft_pending_human_review")
        self.assertIn("Do not generate", self.rules["review_gate"])
        self.assertEqual(self.rules["rule_model_counts"], {"named_link": 13, "page_identity": 27})
        self.assertEqual(
            self.rules["label_counts"],
            {"content_list": 16, "landing_page": 19, "product_catalog": 5},
        )
        for identifier, rule in rules.items():
            validate_acceptance_contract(rule["acceptance_contract"])
            self.assertEqual(rule["domain"], selected[identifier]["domain"])
            self.assertEqual(rule["label"], selected[identifier]["label"])
            self.assertEqual(rule["review_status"], "draft_pending_human_review")
            self.assertFalse(rule["evidence"]["provider_output_used"])
            self.assertFalse(rule["evidence"]["clicks_or_recursive_navigation"])
            if rule["rule_model"] == "named_link":
                self.assertGreaterEqual(rule["observed_matching_named_link_count"], 3)
                self.assertEqual(len(rule["observed_named_link_examples"]), 3)
            else:
                self.assertEqual(rule["observed_matching_named_link_count"], 0)
                self.assertEqual(rule["observed_named_link_examples"], [])

    def test_rules_are_hash_bound_to_selection_and_capture(self):
        for path_key, hash_key in (
            ("source_selection", "source_selection_sha256"),
            ("source_capture", "source_capture_sha256"),
        ):
            path = ROOT / self.rules[path_key]
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.rules[hash_key])


if __name__ == "__main__":
    unittest.main()
