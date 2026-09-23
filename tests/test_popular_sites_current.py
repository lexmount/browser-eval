import hashlib
import json
import unittest
from pathlib import Path

from harness.web_fetch.stable_tasks import validate_acceptance_contract


ROOT = Path(__file__).resolve().parents[1]
TRACK = ROOT / "evaluation/popular-sites"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PopularSitesCurrentTests(unittest.TestCase):
    def test_current_dataset_registry_and_profile_are_one_complete_matrix(self):
        dataset = json.loads((TRACK / "dataset.json").read_text())
        review_path = TRACK / "review.json"
        review = json.loads(review_path.read_text())
        profile = json.loads((TRACK / "current-report.json").read_text())

        cases = dataset["cases"]
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({case["id"] for case in cases}), 100)
        self.assertEqual(profile["dataset_id"], dataset["id"])
        self.assertEqual(profile["case_count"], 100)
        self.assertEqual(profile["providers"], ["moli-webfetch"])
        self.assertEqual(profile["artifact_site_base_url"], "https://artifact-site.lexmount.cn")
        self.assertEqual(
            dataset["comparison"]["rule_revision"]["review_sha256"], sha256(review_path)
        )
        self.assertEqual(
            review["case_rules_sha256"],
            hashlib.sha256(json.dumps(
                [(case["id"], case["url"], case["acceptance_contract"])
                 for case in cases],
                ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            ).encode()).hexdigest(),
        )
        self.assertNotIn("source_rule_registry", dataset["comparison"])
        self.assertNotIn("execution_contract", dataset["comparison"])

    def test_every_case_has_hash_bound_current_source_and_valid_contract(self):
        dataset = json.loads((TRACK / "dataset.json").read_text())
        for case in dataset["cases"]:
            with self.subTest(case=case["id"]):
                validate_acceptance_contract(case["acceptance_contract"])
                annotation = case["source_benchmark"]["stable_annotation"]
                relative = Path(annotation["anchor_evidence"])
                self.assertEqual(relative.parts[0], "sources")
                stable_page = TRACK / relative
                evidence = stable_page.with_name("evidence.json")
                self.assertTrue(stable_page.is_file())
                self.assertTrue(evidence.is_file())
                self.assertEqual(annotation["anchor_evidence_sha256"], sha256(stable_page))
                metadata = json.loads(evidence.read_text())
                self.assertEqual(metadata["case_id"], case["id"])
                self.assertEqual(metadata["url"], case["url"])
                self.assertEqual(metadata["stable_page_sha256"], sha256(stable_page))
                self.assertFalse(metadata["provider_output_used"])


if __name__ == "__main__":
    unittest.main()
