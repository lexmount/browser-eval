import unittest
from pathlib import Path

from browser_harness_eval.config import load_dataset


class GroundTruthTest(unittest.TestCase):
    def test_every_case_has_reviewable_ground_truth(self) -> None:
        metadata, cases = load_dataset(Path("datasets/public_webfetch.json"))
        self.assertEqual(metadata["id"], "public-webfetch-v3.0-hil")
        self.assertEqual(metadata["ground_truth"]["verified_at"], "2026-09-01")
        for case in cases:
            self.assertGreaterEqual(len(case.required_facts), 3, case.id)
            self.assertGreater(case.min_content_chars, 1, case.id)
            self.assertTrue(case.ground_truth_evidence, case.id)
            self.assertTrue(case.annotation_notes, case.id)
            self.assertTrue(case.task_prompt, case.id)
            self.assertTrue(case.reference_answer, case.id)
            self.assertGreaterEqual(len(case.human_review_checklist), 3, case.id)
            for fact in case.required_facts:
                self.assertTrue(fact.get("id"), case.id)
                self.assertTrue(fact.get("alternatives"), f"{case.id}/{fact.get('id')}")

    def test_expanded_suite_has_fifty_stratified_cases(self) -> None:
        metadata, cases = load_dataset(Path("datasets/public_webfetch_50.json"))
        self.assertEqual(len(cases), 50)
        self.assertEqual(len({case.id for case in cases}), 50)
        self.assertEqual(sum(case.id.startswith("wcxb-") for case in cases), 28)
        self.assertEqual(sum(case.id.startswith("lm-") for case in cases), 12)
        for case in cases:
            self.assertGreaterEqual(len(case.required_facts), 2, case.id)
            self.assertTrue(case.reference_answer, case.id)
            self.assertGreaterEqual(len(case.human_review_checklist), 3, case.id)

    def test_direct_media_cases_do_not_use_webpage_content_score(self) -> None:
        _, cases = load_dataset(Path("datasets/file_media_capabilities.json"))
        by_id = {case.id: case for case in cases}
        self.assertFalse(by_id["direct-image"].content_score_applicable)
        self.assertFalse(by_id["direct-video"].content_score_applicable)
        self.assertTrue(by_id["embedded-image-page"].content_score_applicable)


if __name__ == "__main__":
    unittest.main()
