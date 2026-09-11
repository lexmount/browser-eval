import hashlib
import json
from pathlib import Path
import unittest

from harness.web_fetch.config import load_dataset
from harness.web_fetch.stable_tasks import validate_acceptance_contract


ROOT = Path(__file__).resolve().parents[1]


class GlobalHomepageSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = json.loads((ROOT / "research/global-homepage-selected-pages.json").read_text())
        cls.rules = json.loads((ROOT / "research/global-homepage-rules.json").read_text())
        cls.dataset_payload, cls.dataset = load_dataset(ROOT / "evaluation/global-homepages-dataset.json")

    def test_final_selection_partitions_policy_filtered_candidates(self):
        candidates = json.loads((ROOT / "research/similarweb-global-candidate-domains-2026-05.json").read_text())
        selected = {case["domain"] for case in self.selection["cases"]}
        excluded = {case["domain"] for case in self.selection["excluded"]}
        self.assertEqual(len(selected), 52)
        self.assertEqual(len(excluded), 38)
        self.assertFalse(selected & excluded)
        self.assertEqual(selected | excluded, set(candidates["domains"]))
        url_overrides = {
            "google.com": "https://www.google.com/?hl=en",
            "youtube.com": "https://www.youtube.com/?hl=en&gl=US&persist_hl=1",
            "yahoo.com": "https://www.yahoo.com/?p=us",
            "twitch.tv": "https://www.twitch.tv/?lang=en",
            "duckduckgo.com": "https://duckduckgo.com/?kad=en_US&kl=us-en",
            "booking.com": "https://www.booking.com/?lang=en-us",
            "spotify.com": "https://open.spotify.com/?locale=en_US",
            "deepseek.com": "https://www.deepseek.com/en/",
            "shein.com": "https://us.shein.com/",
            "primevideo.com": "https://www.primevideo.com/-/zh/storefront",
        }
        for case in self.selection["cases"]:
            self.assertEqual(case["url"], url_overrides.get(case["domain"], f"https://{case['domain']}/"))
            self.assertEqual(case["rule_status"], "ready_from_final_independent_browser_evidence")
        for domain in ("temu.com", "walmart.com", "facebook.com", "x.com", "imdb.com"):
            self.assertIn(domain, excluded)
        self.assertIn("roblox.com", excluded)

    def test_rules_and_dataset_exactly_cover_selected_cases(self):
        selected_ids = {case["id"] for case in self.selection["cases"]}
        rules = {case["id"]: case for case in self.rules["cases"]}
        dataset = {case.id: case for case in self.dataset}
        self.assertEqual(set(rules), selected_ids)
        self.assertEqual(set(dataset), selected_ids)
        self.assertEqual(self.dataset_payload["coverage_plan"], {"challenge": ["lexmount-global"]})
        self.assertEqual(sum(rule["rule_model"] == "named_link" for rule in rules.values()), 19)
        self.assertEqual(sum(rule["rule_model"] == "page_identity" for rule in rules.values()), 33)
        for identifier, rule in rules.items():
            validate_acceptance_contract(rule["acceptance_contract"])
            self.assertEqual(dataset[identifier].url, rule["url"])
            self.assertEqual(dataset[identifier].min_content_chars, 1)
            self.assertFalse(dataset[identifier].content_score_applicable)
            if rule["rule_model"] == "named_link":
                self.assertGreaterEqual(rule["observed_matching_named_links"], 3)
            else:
                self.assertEqual(rule["observed_matching_named_links"], 0)

        youtube = next(case for case in self.selection["cases"] if case["domain"] == "youtube.com")
        youtube_rule = rules[youtube["id"]]
        self.assertEqual(youtube["label"], "content_list")
        self.assertEqual(youtube_rule["rule_model"], "page_identity")
        self.assertEqual(youtube_rule["anchor_groups"], [
            ["YouTube Home"], ["Guide"], ["Search"], ["Settings"], ["Sign in"],
        ])

        google = next(case for case in self.selection["cases"] if case["domain"] == "google.com")
        self.assertEqual(len(rules[google["id"]]["anchor_groups"]), 6)
        self.assertEqual(rules[google["id"]]["anchor_groups"][1], ["About"])

        reviewed_domains = {
            "google.com", "youtube.com", "chatgpt.com", "bing.com",
            "gemini.google.com", "yahoo.co.jp", "yahoo.com", "canva.com",
            "duckduckgo.com", "spotify.com", "booking.com", "deepseek.com", "rakuten.co.jp",
            "shein.com", "primevideo.com",
        }
        for case in self.selection["cases"]:
            if case["domain"] in reviewed_domains:
                self.assertTrue(all(len(group) == 1 for group in rules[case["id"]]["anchor_groups"]))

    def test_anchor_evidence_is_present_and_hash_bound(self):
        for case in self.dataset:
            annotation = case.source_benchmark["stable_annotation"]
            evidence = ROOT / "evaluation" / annotation["anchor_evidence"]
            self.assertTrue(evidence.is_file(), evidence)
            self.assertEqual(hashlib.sha256(evidence.read_bytes()).hexdigest(),
                             annotation["anchor_evidence_sha256"])
            record = json.loads(evidence.with_name("evidence.json").read_text())
            self.assertEqual(record["case_id"], case.id)
            self.assertEqual(record["url"], case.url)
            self.assertEqual(record["stable_page_sha256"], annotation["anchor_evidence_sha256"])
            self.assertFalse(record["provider_output_used"])
            self.assertFalse(record["clicks_or_recursive_navigation"])

    def test_rule_and_selection_hash_chain(self):
        selected_path = ROOT / self.rules["source_selection"]
        self.assertEqual(hashlib.sha256(selected_path.read_bytes()).hexdigest(),
                         self.rules["source_selection_sha256"])
        rules_path = ROOT / self.dataset_payload["comparison"]["source_rule_registry"]
        self.assertEqual(hashlib.sha256(rules_path.read_bytes()).hexdigest(),
                         self.dataset_payload["comparison"]["source_rule_registry_sha256"])
        visual_path = ROOT / self.dataset_payload["comparison"]["preflight_visual_spot_check"]
        visual_hash = hashlib.sha256(visual_path.read_bytes()).hexdigest()
        self.assertEqual(visual_hash,
                         self.dataset_payload["comparison"]["preflight_visual_spot_check_sha256"])
        self.assertEqual(visual_hash, self.rules["visual_spot_check_sha256"])
        variant_path = ROOT / self.rules["variant_review"]
        variant_hash = hashlib.sha256(variant_path.read_bytes()).hexdigest()
        self.assertEqual(variant_hash, self.rules["variant_review_sha256"])
        self.assertEqual(
            variant_hash,
            self.dataset_payload["comparison"]["rule_variant_review_sha256"],
        )
        locale_path = ROOT / self.rules["locale_review"]
        locale_hash = hashlib.sha256(locale_path.read_bytes()).hexdigest()
        self.assertEqual(locale_hash, self.rules["locale_review_sha256"])
        self.assertEqual(locale_hash, self.dataset_payload["comparison"]["locale_review_sha256"])


if __name__ == "__main__":
    unittest.main()
