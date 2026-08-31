import unittest
from pathlib import Path

from browser_harness_eval.config import load_dataset


class GroundTruthTest(unittest.TestCase):
    def test_every_case_has_reviewable_ground_truth(self) -> None:
        metadata, cases = load_dataset(Path("datasets/public_webfetch.json"))
        self.assertEqual(metadata["id"], "public-webfetch-v2.1")
        self.assertEqual(metadata["ground_truth"]["verified_at"], "2026-08-31")
        for case in cases:
            self.assertGreaterEqual(len(case.required_facts), 3, case.id)
            self.assertGreater(case.min_content_chars, 1, case.id)
            self.assertTrue(case.ground_truth_evidence, case.id)
            self.assertTrue(case.annotation_notes, case.id)
            for fact in case.required_facts:
                self.assertTrue(fact.get("id"), case.id)
                self.assertTrue(fact.get("alternatives"), f"{case.id}/{fact.get('id')}")


if __name__ == "__main__":
    unittest.main()
