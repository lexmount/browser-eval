from tests.web_search.fixtures import execution_profile
"""Search scenarios: constraints, missing judgments, costs and evidence integrity."""

import copy
import tempfile
import unittest
from pathlib import Path

from harness.web_search.core import aggregate, normalize, request_body, validate_profile, verify_bundle
from lexbench_websearch.scoring import evaluate


def result(url, text="Evidence"):
    return dict(url=url, title="Document", snippet=text, summary="", content="")


class SearchScenarios(unittest.TestCase):


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
        profile = execution_profile()
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
        profile = execution_profile()
        profile["protocol"]["preserve_publisher_queries"] = True
        profile["cases"][0]["query"] = "x" * 325
        validate_profile(profile)
        bad = copy.deepcopy(profile)
        bad["cases"][0]["query"] = "x" * 4097
        with self.assertRaises(ValueError):
            validate_profile(bad)

    def test_profile_rejects_invalid_reference_urls_before_calls(self):
        profile = execution_profile()
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






class ConstraintOutcomeScenarios(unittest.TestCase):
    def test_site_presence_uses_the_same_scoped_acceptance_as_comparison(self):
        from lexbench_websearch.scoring import classify

        case = {"family": "site", "domains": ["example.org"]}
        results = [result("https://a.example.org/"), result("https://outside.org/")]
        row = dict(http_status=200, error="", results=results, metrics=evaluate(case, results))
        self.assertEqual(classify(case, row), ("pass", "domain_hit"))
        from harness.web_search.comparison import acceptance
        self.assertTrue(acceptance(case, row, "primary"))



def test_ten_result_task_uses_the_same_acceptance_as_its_classifier():
    from lexbench_websearch.scoring import classify, task_acceptance
    case = {"id": "q", "anchors": ["https://example.org/answer"]}
    row = {"results": [{"url": "https://example.org/" + str(i)} for i in range(9)] + [{"url": case["anchors"][0]}]}
    assert task_acceptance(case, row, cutoff=10)
    assert classify(case, row, cutoff=10)[0] == "pass"
    assert not task_acceptance(case, row, cutoff=5)
    assert classify(case, row, cutoff=5)[0] == "fail"
