"""Checks for the unrun representative-content extension cohort."""

import hashlib
import json
from pathlib import Path
import unittest

from harness.web_fetch.config import load_dataset
from harness.web_fetch.stable_tasks import validate_acceptance_contract


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/global-representative-content-dataset.json"


class GlobalRepresentativeDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload, cls.cases = load_dataset(DATASET)
        cls.default = json.loads((ROOT / "evaluation/dataset.json").read_text())

    def test_is_a_separate_unrun_45_case_cohort(self):
        self.assertEqual(len(self.cases), 45)
        self.assertEqual(
            self.payload["comparison"]["publication_state"],
            "dataset_only_not_run",
        )
        self.assertEqual(self.payload["coverage_plan"], {"challenge": []})
        default_ids = {case["id"] for case in self.default["cases"]}
        self.assertTrue(default_ids.isdisjoint(case.id for case in self.cases))

    def test_contracts_and_compact_evidence_are_bound(self):
        for case in self.cases:
            validate_acceptance_contract(case.acceptance_contract)
            self.assertEqual(
                case.source_benchmark["selection_role"],
                "extension_cohort_not_ranked_domain_homepage",
            )
            relative = case.source_benchmark["stable_annotation"]["anchor_evidence"]
            evidence_path = ROOT / "evaluation" / relative
            expected = case.source_benchmark["stable_annotation"]["anchor_evidence_sha256"]
            self.assertEqual(hashlib.sha256(evidence_path.read_bytes()).hexdigest(), expected)
            evidence = json.loads(evidence_path.with_name("evidence.json").read_text())
            self.assertFalse(evidence["provider_output_used"])
            self.assertEqual(evidence["stable_page_sha256"], expected)


if __name__ == "__main__":
    unittest.main()
