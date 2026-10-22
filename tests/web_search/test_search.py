"""Search scenarios: constraints, missing judgments, costs and evidence integrity."""

import copy
import tempfile
import unittest
from pathlib import Path

from harness.web_search.core import (
    aggregate,
    canonical_url,
    evaluate,
    normalize,
    request_body,
    validate_profile,
    verify_bundle,
)


def result(url, text="Evidence"):
    return dict(url=url, title="Document", snippet=text, summary="", content="")


class SearchScenarios(unittest.TestCase):
    def test_labeled_fact_evidence_is_separate_from_relevance(self):
        case = dict(reference_answer="Ada Lovelace", reference_urls=["https://example.org/a"])
        positive = result("https://example.org/a?utm_source=x", "ADA, Lovelace wrote it.")
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
        self.assertEqual(evaluate(dict(reference_answer="1900"), [result("https://a.org", "19001")])["answer_in_preview_5"], 0)
        row = result("https://a.org", "Lovelace")
        row["title"] = "Ada"
        self.assertEqual(evaluate(dict(reference_answer="Ada Lovelace"), [row])["answer_in_preview_5"], 0)

    def test_optional_content_modes_preserve_query_constraints_and_budget(self):
        case = dict(query="site:example.org research after:2026-09-01", family="site",
                    domains=["example.org"])
        protocol = dict(top_k=5, server_timeout_ms=10000, as_of="2026-10-20")
        for adapter, option in (("deepseek", "need_content"), ("bocha", "summary")):
            disabled = request_body(adapter, case, protocol, {option: False})
            enabled = request_body(adapter, case, protocol, {option: True})
            self.assertFalse(disabled.pop(option))
            self.assertTrue(enabled.pop(option))
            self.assertEqual(disabled, enabled)
            self.assertEqual(disabled["count"], 5)
        with self.assertRaises(ValueError):
            request_body("deepseek", case, protocol, {"need_content": "false"})
        with self.assertRaises(ValueError):
            request_body("lexmount", case, protocol, {"summary": True})

    def test_tracking_duplicates_do_not_create_extra_hits(self):
        case = dict(anchors=["https://example.org/p"], domains=[])
        score = evaluate(
            case,
            [result("https://example.org/p?utm_source=x"), result("https://example.org/p#part")],
        )
        self.assertEqual(score["anchor_hit_5"], 1)
        self.assertEqual(score["duplicate_rate"], 0.5)
        self.assertIsNone(score["ndcg_10"])

    def test_domain_suffix_attack_and_empty_response_are_not_constraint_success(self):
        case = dict(domains=["github.com"], anchors=[])
        self.assertEqual(
            evaluate(case, [result("https://github.com.evil.org/a")])["domain_precision"], 0
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

    def test_site_search_is_equivalent_intent_but_explicit_different_route(self):
        case = dict(query="Attention Is All You Need", family="vertical", source="arxiv", domains=["arxiv.org"])
        protocol = dict(top_k=10, server_timeout_ms=10000)
        self.assertEqual(request_body("lexmount", case, protocol)["source"], "arxiv")
        self.assertEqual(
            request_body("deepseek", case, protocol)["query"],
            "Attention Is All You Need site:arxiv.org",
        )
        self.assertTrue(request_body("deepseek", case, protocol)["need_content"])
        bocha = request_body("bocha", case, protocol)
        self.assertEqual(bocha["count"], 10)
        self.assertTrue(bocha["summary"])
        self.assertEqual(bocha["query"], case["query"])
        self.assertEqual(bocha["include"], "arxiv.org")

    def test_bocha_web_pages_are_scored_as_search_results_without_invented_page_body(self):
        payload = {"code": 200, "data": {"webPages": {"value": [
            {"url": "https://example.org/p", "name": "Example", "snippet": "Short", "summary": "Long"}
        ]}}}
        rows = normalize(payload)
        self.assertEqual(rows[0]["title"], "Example")
        self.assertEqual(rows[0]["summary"], "Long")
        self.assertEqual(rows[0]["content"], "")

    def test_invalid_payload_does_not_become_empty_success(self):
        for bad in ({}, {"results": None}, {"results": [{"title": "no url"}]}, {"error": "bad"}):
            with self.assertRaises(ValueError):
                normalize(bad)
        self.assertEqual(normalize({"results": []}), [])

    def test_all_call_latency_preserves_failures_and_missing_cost(self):
        rows = [
            dict(
                method="a",
                elapsed_ms=100,
                http_status=200,
                results=[result("https://a.org")],
                cost=None,
                metrics=evaluate({}, []),
            ),
            dict(
                method="a",
                elapsed_ms=900,
                http_status=504,
                results=[],
                cost=None,
                metrics=evaluate({}, []),
            ),
        ]
        stats = aggregate(rows)["a"]
        self.assertEqual(stats["all_latency"]["mean"], 500)
        self.assertEqual(stats["success_latency"]["mean"], 100)
        self.assertIsNone(stats["cost_total"])

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

    def test_bocha_uses_native_site_and_date_filters_with_five_results(self):
        from harness.web_search.core import request_body

        case = {
            "family": "site",
            "query": "site:example.org research after:2026-09-01",
            "domains": ["example.org"],
        }
        protocol = {"top_k": 5, "as_of": "2026-10-20"}
        self.assertEqual(
            request_body("bocha", case, protocol),
            {
                "query": "research",
                "count": 5,
                "summary": True,
                "freshness": "2026-09-02..2026-10-20",
                "include": "example.org",
            },
        )
        self.assertEqual(
            request_body("deepseek", case, protocol)["query"], case["query"]
        )

    def test_profile_rejects_duplicate_cases_and_oversize_deepseek_query(self):
        from harness.web_search.profiles import load_profile
        profile = load_profile()
        validate_profile(profile)
        bad = copy.deepcopy(profile)
        bad["cases"].append(bad["cases"][0])
        with self.assertRaises(ValueError):
            validate_profile(bad)
        bad = copy.deepcopy(profile)
        bad["cases"][0]["query"] = "x" * 129
        with self.assertRaises(ValueError):
            validate_profile(bad)

    def test_publisher_sample_can_preserve_a_long_query(self):
        from harness.web_search.profiles import load_profile
        profile = load_profile()
        profile["protocol"]["preserve_publisher_queries"] = True
        profile["cases"][0]["query"] = "x" * 325
        validate_profile(profile)
        bad = copy.deepcopy(profile)
        bad["cases"][0]["query"] = "x" * 4097
        with self.assertRaises(ValueError):
            validate_profile(bad)

    def test_profile_rejects_invalid_reference_urls_before_calls(self):
        from harness.web_search.profiles import load_profile
        profile = load_profile()
        profile["cases"][0]["reference_urls"] = ["brokenhttps://example.org"]
        with self.assertRaisesRegex(ValueError, "HTTP"):
            validate_profile(profile)

    def test_bundle_rejects_tampering_and_symlinks(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "a.json").write_text("{}")
            binding = {"path": "a.json", "sha256": hashlib.sha256(b"{}").hexdigest()}
            verify_bundle(p, [binding])
            (p / "a.json").write_text('{"changed":1}')
            with self.assertRaises(ValueError):
                verify_bundle(p, [binding])
            (p / "link.json").symlink_to(p / "a.json")
            binding["path"] = "link.json"
            with self.assertRaises(ValueError):
                verify_bundle(p, [binding])


if __name__ == "__main__":
    unittest.main()


class ReferenceIdentityScenarios(unittest.TestCase):
    def test_paper_formats_match_declared_paper_but_citations_do_not(self):
        case = {"anchors": ["https://arxiv.org/abs/2504.13128"], "paper_ids": ["2504.13128"]}
        self.assertEqual(
            evaluate(case, [result("https://web3.arxiv.org/pdf/2504.13128#7#4")])["anchor_hit_10"],
            1,
        )
        self.assertEqual(
            evaluate(case, [result("https://arxiv.org/pdf/2504.13129")])["anchor_hit_10"], 0
        )
        self.assertEqual(
            evaluate(case, [result("https://arxiv.org.evil.org/pdf/2504.13128")])["anchor_hit_10"],
            0,
        )
        self.assertEqual(
            evaluate(case, [result("https://arxiv.org/list/cs?paper=2504.13128")])["anchor_hit_10"],
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
            evaluate(case, [result("https://arxiv.org/pdf/2504.13128")])["anchor_hit_10"], 0
        )


class ConstraintOutcomeScenarios(unittest.TestCase):
    def test_site_presence_uses_the_same_scoped_acceptance_as_comparison(self):
        from harness.web_search.core import classify

        case = {"family": "site", "domains": ["example.org"]}
        results = [result("https://a.example.org/"), result("https://outside.org/")]
        row = dict(http_status=200, error="", results=results, metrics=evaluate(case, results))
        self.assertEqual(classify(case, row), ("pass", "domain_hit"))
        from harness.web_search.comparison import acceptance
        self.assertTrue(acceptance(case, row, "primary"))

    def test_empty_and_api_failure_stay_distinct(self):
        from harness.web_search.core import classify

        row = dict(http_status=200, error="", results=[], metrics=evaluate({}, []))
        self.assertEqual(classify({"family": "web"}, row), ("fail", "empty"))
        self.assertEqual(
            classify({"family": "web"}, {**row, "http_status": 503}), ("fail", "api_error")
        )
