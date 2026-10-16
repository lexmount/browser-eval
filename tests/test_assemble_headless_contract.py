import unittest

from harness.web_fetch.assembly import _provider_request_contract


class ProviderRequestContractTests(unittest.TestCase):
    def test_ignores_reader_description_and_reference_evidence(self):
        case = {
            "id": "page",
            "url": "https://example.test/",
            "task_prompt": "Return the page body.",
            "description": "Earlier reader-facing explanation.",
            "ground_truth_evidence": {"source": "earlier observation"},
        }
        canonical = {
            **case,
            "description": "Corrected reader-facing explanation.",
            "ground_truth_evidence": {"source": "current observation"},
        }
        self.assertEqual(
            _provider_request_contract(case, "crawl4ai"),
            _provider_request_contract(canonical, "crawl4ai"),
        )

    def test_preserves_fields_that_change_the_physical_request(self):
        case = {
            "id": "page",
            "url": "https://example.test/",
            "task_prompt": "Return the page body.",
            "acceptance_contract": {"body_requirements": ["Current body"]},
        }
        for field, changed in (
                ("url", "https://other.example.test/"),
                ("task_prompt", "Return a different scope."),
                ("acceptance_contract", {"body_requirements": ["Different body"]})):
            with self.subTest(field=field):
                canonical = {**case, field: changed}
                self.assertNotEqual(
                    _provider_request_contract(case, "crawl4ai"),
                    _provider_request_contract(canonical, "crawl4ai"),
                )

    def test_ignores_draft_mutable_body_annotation_that_never_entered_request(self):
        case = {
            "id": "body",
            "url": "https://example.test/",
            "source_benchmark": {
                "snapshot_metric_scope": "captured_snapshot_diagnostic",
                "stable_annotation": {"content_kind": "changing_public_body"},
            },
            "mutable_body_policy": {"stable_annotation": {"content_kind": "changing_public_body"}},
        }
        canonical = {
            "id": "body",
            "url": "https://example.test/",
            "source_benchmark": {},
            "mutable_body_policy": case["mutable_body_policy"],
        }
        self.assertEqual(
            _provider_request_contract(case, "crawl4ai"),
            _provider_request_contract(canonical, "crawl4ai"),
        )

    def test_preserves_dynamic_list_source_contract(self):
        case = {
            "id": "list",
            "source_benchmark": {
                "snapshot_metric_scope": "captured_sample_diagnostic",
                "stable_annotation": {"content_kind": "changing_public_list"},
            },
        }
        self.assertEqual(_provider_request_contract(case, "crawl4ai"), case)

    def test_native_dataset_removes_named_contracts_before_request_binding(self):
        from harness.web_fetch.retrieval_hints import native_fetch_dataset
        source = {"tool": "web_fetch", "cases": [
            {"id": "arbitrary", "url": "https://example.test/", "track": "dynamic",
             "retrieval_hints": {"version": 3, "mode": "native",
                                  "contract": "case-specific-contract"}},
        ]}
        selected = native_fetch_dataset(source)["cases"][0]
        expected = {"id": "arbitrary", "url": "https://example.test/", "track": "dynamic",
                    "retrieval_hints": {"version": 3, "mode": "native"}}
        for provider in ("moli-webfetch", "moli-pr919-webfetch", "lightpanda",
                         "obscura", "chromium", "kitesurf"):
            self.assertEqual(_provider_request_contract(selected, provider),
                             _provider_request_contract(expected, provider))


if __name__ == "__main__":
    unittest.main()
