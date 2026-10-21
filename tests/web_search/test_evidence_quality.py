"""A leaked answer must not improve retrieval evidence or move a later hit up."""
import unittest

from harness.web_search.evidence_quality import (
    LEGACY_CONTAMINATION_RULE, factual_sensitivity, is_benchmark_page,
)


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

    def test_exact_question_with_evaluation_fields_is_not_source_evidence(self):
        case = {"query": "Who wrote the analytical engine notes?", "reference_answer": "Ada", "reference_urls": []}
        dump = {"url": "https://github.com/example/results", "content": 'Who wrote the analytical engine notes? ground_truth: Ada; model_answer: Ada'}
        article = {"url": "https://history.org/article", "snippet": "Who wrote the analytical engine notes? Ada wrote them."}
        result = factual_sensitivity(case, [dump, article])
        self.assertEqual(result["excluded_ranks"], [1])
        self.assertEqual(result["metrics"]["answer_in_preview_5"], 1)
        self.assertFalse(is_benchmark_page(article, case=case))
        self.assertFalse(is_benchmark_page({"url": "https://huggingface.co/model", "content": "ground_truth labels for another question"}, case=case))
        self.assertEqual(factual_sensitivity(case, [dump], rule=LEGACY_CONTAMINATION_RULE)["excluded_ranks"], [])
        self.assertEqual(factual_sensitivity(case, [dump])["metrics"]["answer_in_preview_5"], 0)

    def test_dataset_viewers_and_validation_logs_are_not_independent_answers(self):
        from harness.web_search.evidence_quality import EVALUATION_FIELDS_RULE
        case = {"query": "What is the founding year of the college?", "reference_answer": "1971", "reference_urls": []}
        formats = [
            'HIDDEN LOG ERROR: Answer validation failed Expected: 1971 Received: [user_input]',
            '1971 {"loc":"unrelated question", "loc_ans":"someone"} Subsets and Splits SELECT * FROM train LIMIT 10',
            '["1969", "1971", "2000"] 1971 H Science easy false Subsets and Splits SELECT * FROM train LIMIT 10',
        ]
        for text in formats:
            result = {"url": "https://example.org/record", "content": case["query"] + " " + text}
            assert is_benchmark_page(result, case=case)
            assert not is_benchmark_page(result, case=case, rule=EVALUATION_FIELDS_RULE)
            assert factual_sensitivity(case, [result])["metrics"]["answer_in_delivered_text_5"] == 0
        article = {"url": "https://college.org/history", "content": case["query"] + " It opened in 1971. Our training records are maintained separately."}
        assert not is_benchmark_page(article, case=case)
        assert not is_benchmark_page({"url": "https://huggingface.co/tutorial", "content": "Subsets and Splits SELECT * FROM train LIMIT 10"}, case=case)
