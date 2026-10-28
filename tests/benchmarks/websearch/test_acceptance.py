import unittest
from lexbench_websearch.scoring import canonical_url, evaluate


def result(url, text="Evidence"):
    return dict(url=url, title="Document", snippet=text, summary="", content="")


class AcceptanceScenarios(unittest.TestCase):
    def test_labeled_fact_evidence_is_separate_from_relevance(self):
        case = dict(
            reference_answer="Ada Lovelace", reference_urls=["https://example.org/a"]
        )
        positive = result(
            "https://example.org/a?utm_source=x", "ADA, Lovelace wrote it."
        )
        score = evaluate(case, [positive])
        self.assertEqual(score["labeled_source_hit_5"], 1)
        self.assertEqual(score["answer_in_preview_5"], 1)
        self.assertIsNone(score["ndcg_10"])
        negative = result("https://example.org/another", "Adaline Lovelace")
        score = evaluate(case, [negative])
        self.assertEqual(score["labeled_source_hit_5"], 0)
        self.assertEqual(score["answer_in_preview_5"], 0)
        negative["summary"] = "Ada Lovelace"
        self.assertEqual(evaluate(case, [negative])["answer_in_delivered_text_5"], 1)
        self.assertEqual(evaluate(case, [negative])["answer_in_preview_5"], 0)
        self.assertEqual(evaluate(case, [])["answer_in_preview_5"], 0)

    def test_numeric_fact_does_not_match_part_of_larger_number_or_across_fields(self):
        self.assertEqual(
            evaluate(dict(reference_answer="1900"), [result("https://a.org", "19001")])[
                "answer_in_preview_5"
            ],
            0,
        )
        row = result("https://a.org", "Lovelace")
        row["title"] = "Ada"
        self.assertEqual(
            evaluate(dict(reference_answer="Ada Lovelace"), [row])[
                "answer_in_preview_5"
            ],
            0,
        )

    def test_tracking_duplicates_do_not_create_extra_hits(self):
        case = dict(anchors=["https://example.org/p"], domains=[])
        score = evaluate(
            case,
            [
                result("https://example.org/p?utm_source=x"),
                result("https://example.org/p#part"),
            ],
        )
        self.assertEqual(score["anchor_hit_5"], 1)
        self.assertEqual(score["duplicate_rate"], 0.5)
        self.assertIsNone(score["ndcg_10"])

    def test_domain_suffix_attack_and_empty_response_are_not_constraint_success(self):
        case = dict(domains=["github.com"], anchors=[])
        self.assertEqual(
            evaluate(case, [result("https://github.com.evil.org/a")])[
                "domain_precision"
            ],
            0,
        )
        self.assertEqual(
            evaluate(case, [result("https://gist.github.com/a")])["domain_precision"], 1
        )
        self.assertIsNone(evaluate(case, [])["domain_precision"])

    def test_relevant_result_must_be_ranked_within_cutoff(self):
        case = dict(anchors=["https://example.org/answer"])
        rows = [result(f"https://example.org/wrong/{n}") for n in range(5)]
        rows.append(result("https://example.org/answer"))
        score = evaluate(case, rows)
        self.assertEqual(score["anchor_hit_5"], 0)
        self.assertEqual(score["anchor_hit_10"], 1)
        self.assertAlmostEqual(score["reciprocal_rank"], 1 / 6)

    def test_incomplete_judgments_never_become_zero_relevance(self):
        case = dict(qrels={"https://a.org/": 3})
        self.assertIsNone(evaluate(case, [result("https://b.org/")])["ndcg_10"])
        self.assertEqual(evaluate(case, [result("https://a.org/")])["ndcg_10"], 1)

    def test_duplicate_cannot_inflate_ndcg(self):
        case = dict(qrels={"https://a.org/": 3, "https://b.org/": 2})
        score = evaluate(case, [result("https://a.org/"), result("https://a.org/")])
        self.assertLess(score["ndcg_10"], 1)

    def test_semantic_url_parameters_are_preserved(self):
        self.assertNotEqual(
            canonical_url("https://a.org/?id=1"), canonical_url("https://a.org/?id=2")
        )

    def test_known_tracking_parameters_do_not_hide_the_same_page(self):
        target = "https://peps.python.org/pep-0703/"
        tracked = target + "?_bhlid=tracking-value"
        self.assertEqual(canonical_url(target), canonical_url(tracked))
        self.assertEqual(
            evaluate({"anchors": [target]}, [result(tracked)])["anchor_hit_5"], 1
        )

    def test_paper_formats_match_declared_paper_but_citations_do_not(self):
        case = {
            "anchors": ["https://arxiv.org/abs/2504.13128"],
            "paper_ids": ["2504.13128"],
        }
        self.assertEqual(
            evaluate(case, [result("https://web3.arxiv.org/pdf/2504.13128#7#4")])[
                "anchor_hit_10"
            ],
            1,
        )
        self.assertEqual(
            evaluate(case, [result("https://arxiv.org/pdf/2504.13129")])[
                "anchor_hit_10"
            ],
            0,
        )
        self.assertEqual(
            evaluate(case, [result("https://arxiv.org.evil.org/pdf/2504.13128")])[
                "anchor_hit_10"
            ],
            0,
        )
        self.assertEqual(
            evaluate(case, [result("https://arxiv.org/list/cs?paper=2504.13128")])[
                "anchor_hit_10"
            ],
            0,
        )

    def test_declared_repository_accepts_files_but_not_forks_or_lookalikes(self):
        case = {
            "anchors": ["https://github.com/deepseek-ai/deepseek-harness"],
            "repositories": ["deepseek-ai/deepseek-harness"],
        }
        self.assertEqual(
            evaluate(
                case,
                [
                    result(
                        "https://github.com/deepseek-ai/DeepSeek-Harness/blob/master/README.md?plain=1"
                    )
                ],
            )["anchor_hit_10"],
            1,
        )
        for url in [
            "https://github.com/other/deepseek-harness",
            "https://github.com/deepseek-ai/deepseek-harness-fake",
            "https://github.com.evil.org/deepseek-ai/deepseek-harness",
        ]:
            self.assertEqual(evaluate(case, [result(url)])["anchor_hit_10"], 0)

    def test_reference_url_without_entity_contract_stays_exact(self):
        case = {"anchors": ["https://arxiv.org/abs/2504.13128"]}
        self.assertEqual(
            evaluate(case, [result("https://arxiv.org/pdf/2504.13128")])[
                "anchor_hit_10"
            ],
            0,
        )

    def test_empty_and_api_failure_stay_distinct(self):
        from lexbench_websearch.scoring import classify

        row = dict(http_status=200, error="", results=[], metrics=evaluate({}, []))
        self.assertEqual(classify({"family": "web"}, row), ("fail", "empty"))
        self.assertEqual(
            classify({"family": "web"}, {**row, "http_status": 503}),
            ("fail", "api_error"),
        )
