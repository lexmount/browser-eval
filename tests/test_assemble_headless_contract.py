import unittest

from harness.web_fetch.assembly import _provider_request_contract


class ProviderRequestContractTests(unittest.TestCase):
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

    def test_named_native_contract_changes_only_its_declared_case_request(self):
        ordinary = {"id": "ordinary", "url": "https://example.test/",
                    "retrieval_hints": {"version": 3, "mode": "native"}}
        self.assertEqual(_provider_request_contract(ordinary, "moli-webfetch"), ordinary)
        before = {"id": "dynamic-delayed", "url": "https://example.test/",
                  "retrieval_hints": {"version": 3, "mode": "native"}}
        after = {**before, "retrieval_hints": {"version": 3, "mode": "native",
                                                "contract": "delayed-ten-records"}}
        for provider in ("moli-webfetch", "moli-pr919-webfetch", "lightpanda",
                         "obscura", "chromium", "kitesurf"):
            self.assertNotEqual(_provider_request_contract(before, provider),
                                _provider_request_contract(after, provider))


if __name__ == "__main__":
    unittest.main()
