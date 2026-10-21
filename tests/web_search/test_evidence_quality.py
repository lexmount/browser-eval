"""A leaked answer must not improve retrieval evidence or move a later hit up."""
import unittest

from harness.web_search.evidence_quality import factual_sensitivity, is_benchmark_page


class FactualSensitivityScenarios(unittest.TestCase):
    def test_mirror_answer_is_excluded_without_compacting_ranks(self):
        case = {"reference_answer": "Ada Lovelace", "reference_urls": ["https://source.org/a"]}
        rows = [
            {"url": "https://huggingface.co/datasets/someone/simpleqa-verified", "snippet": "Ada Lovelace"},
            {"url": "https://source.org/a", "snippet": "A source without the answer in its preview"},
        ]
        result = factual_sensitivity(case, rows)
        self.assertEqual(result["excluded_ranks"], [1])
        self.assertEqual(result["metrics"]["labeled_source_rr_5"], 0.5)
        self.assertEqual(result["metrics"]["answer_in_preview_5"], 0)
        self.assertEqual(rows[0]["snippet"], "Ada Lovelace")

    def test_real_source_is_not_excluded_merely_for_host_or_snippet_mention(self):
        self.assertFalse(is_benchmark_page({"url": "https://huggingface.co/datasets/research/climate", "title": "Climate observations", "snippet": "Unlike SimpleQA, this dataset..."}))
        self.assertTrue(is_benchmark_page({"url": "https://mirror.org/a", "title": "Simple-QA answer bank"}))
        self.assertTrue(is_benchmark_page({"url": "https://mirror.org/simple_qa/", "title": "Answers"}))

    def test_excluded_fifth_result_does_not_pull_sixth_result_into_budget(self):
        case = {"reference_answer": "Ada", "reference_urls": ["https://source.org/a"]}
        rows = [{"url": f"https://example.org/{i}"} for i in range(4)] + [
            {"url": "https://mirror.org/simpleqa", "snippet": "Ada"},
            {"url": "https://source.org/a", "snippet": "Ada"},
        ]
        self.assertEqual(factual_sensitivity(case, rows)["metrics"]["labeled_source_hit_5"], 0)
