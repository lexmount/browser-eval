import unittest

from scripts.assemble_headless import _provider_request_contract


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


if __name__ == "__main__":
    unittest.main()
