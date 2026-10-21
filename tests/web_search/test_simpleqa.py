import unittest

from harness.web_search.simpleqa import sample_cases, sampled_profile


CSV = b"""original_index,problem,answer,topic,answer_type,multi_step,requires_reasoning,urls
1,First question?,One,Topic,Text,False,False,https://one.test/a)
2,Second question?,Two,Topic,Text,True,False,"https://en.test/wiki/Person_(artist),junkhttps://two.test/b),https://two.test/c)"
3,Third question?,Three,Topic,Text,False,True,https://three.test/a)
"""


class SimpleQASampleScenarios(unittest.TestCase):
    def test_fixed_seed_is_reproducible_and_keeps_questions_unchanged(self):
        first = sample_cases(CSV, size=2, seed=7)
        second = sample_cases(CSV, size=2, seed=7)
        self.assertEqual(first, second)
        self.assertTrue(all(case["query"].endswith("?") for case in first))
        self.assertTrue(all(url.count("(") >= url.count(")") for case in first for url in case["reference_urls"]))
        all_urls = [url for case in first for url in case["reference_urls"]]
        if any("en.test" in url for url in all_urls):
            self.assertIn("https://en.test/wiki/Person_(artist)", all_urls)
            self.assertIn("https://two.test/b", all_urls)

    def test_profile_uses_only_common_serp_modes(self):
        template = {
            "title": "old",
            "methods": [{"id": value} for value in (
                "lexmount", "deepseek", "bocha", "deepseek-content", "bocha-summary"
            )],
            "protocol": {},
        }
        profile = sampled_profile(template, CSV, size=2, seed=9)
        self.assertEqual([m["id"] for m in profile["methods"]], ["lexmount", "deepseek", "bocha"])
        self.assertEqual(profile["protocol"]["sample"]["population"], 1000)
        self.assertIn("no model judge", profile["protocol"]["quality"])

    def test_invalid_sample_size_is_rejected(self):
        with self.assertRaises(ValueError):
            sample_cases(CSV, size=4, seed=1)


if __name__ == "__main__":
    unittest.main()
