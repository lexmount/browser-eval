import unittest

from scripts.finalize_global_homepage_blind_review import (
    classification,
    reviewed_dataset,
    sanitize_execution_paths,
)


class FinalizeGlobalHomepageBlindReviewTests(unittest.TestCase):
    def test_reviewed_dataset_changes_only_post_run_comparison_metadata(self):
        source = {"id": "suite", "cases": [{"id": "case"}], "comparison": {
            "publication_state": "frozen_not_run",
            "require_independent_result_review": False,
            "source_rule_registry_sha256": "old",
        }}
        canonical = {"cases": [{"id": "case"}], "comparison": {
            "source_rule_registry_sha256": "current",
        }}

        result = reviewed_dataset(source, canonical)

        self.assertEqual(result["cases"], source["cases"])
        self.assertEqual(result["comparison"]["publication_state"],
                         "measured_independently_reviewed")
        self.assertTrue(result["comparison"]["require_independent_result_review"])
        self.assertEqual(result["comparison"]["source_rule_registry_sha256"], "current")

    def test_classification_separates_provider_failure_from_contract_failure(self):
        self.assertEqual(classification("global-homepage-016-amazon-com", "fail"),
                         "provider_access_or_extraction_failure")
        self.assertEqual(classification("global-homepage-002-youtube-com", "fail"),
                         "target_retrieved_but_frozen_contract_not_met")
        self.assertEqual(classification("global-homepage-016-amazon-com", "pass"),
                         "verified_contract_pass")

    def test_execution_projection_does_not_publish_workstation_path(self):
        result = sanitize_execution_paths({"batches": [{
            "dataset": {"source": "/Users/person/project/evaluation/dataset.json"},
            "provider_config_source": "/Users/person/project/configs/provider.json",
            "environment": {"hostname": "person-laptop.local"},
            "projection": {
                "source_execution": "/Users/person/.browser-eval/run/execution.json",
                "source_execution_sha256": "abc",
            },
        }]})
        batch = result["batches"][0]
        projection = batch["projection"]
        self.assertNotIn("source_execution", projection)
        self.assertEqual(projection["source_execution_record"], "execution.json")
        self.assertEqual(projection["source_execution_sha256"], "abc")
        self.assertEqual(batch["dataset"]["source"], "dataset.json")
        self.assertEqual(batch["provider_config_source"], "provider.json")
        self.assertEqual(batch["environment"]["hostname"], "local macOS host (redacted)")


if __name__ == "__main__":
    unittest.main()
