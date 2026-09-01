import json
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

    def test_expanded_suite_has_one_hundred_webpage_cases(self) -> None:
        metadata, cases = load_dataset(Path("datasets/public_webfetch_100.json"))
        self.assertEqual(metadata["id"], "public-webfetch-v7.0-100-live-verified")
        self.assertTrue(metadata["policy"]["exclude_invalid_cases"])
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({case.id for case in cases}), 100)
        self.assertEqual(len({case.url for case in cases}), 100)
        self.assertEqual(sum(case.id.startswith("live-") for case in cases), 84)
        self.assertEqual(sum(not case.id.startswith("live-") for case in cases), 16)
        for case in cases:
            self.assertNotIn(case.category, {"pdf", "document_pdf", "document_docx", "document_txt", "media_image", "media_video"})
            self.assertGreaterEqual(len(case.required_facts), 3, case.id)
            normalized_facts = [
                tuple(" ".join(value.split()).casefold() for value in fact["alternatives"])
                for fact in case.required_facts
            ]
            self.assertEqual(len(normalized_facts), len(set(normalized_facts)), case.id)
            self.assertTrue(case.reference_answer, case.id)
            self.assertGreaterEqual(len(case.human_review_checklist), 3, case.id)

        audit = json.loads(Path(metadata["ground_truth"]["live_browser_audit_file"]).read_text(encoding="utf-8"))
        self.assertEqual(audit["dataset_id"], metadata["id"])
        self.assertEqual(audit["summary"]["total"], 100)
        self.assertEqual(audit["summary"]["verified"], 100)
        self.assertEqual({item["case_id"] for item in audit["results"]}, {case.id for case in cases})
        self.assertTrue(all(item["status"] == "verified" for item in audit["results"]))

        deprecated = json.loads(Path("research/deprecated-cases-2026-09-01.json").read_text(encoding="utf-8"))
        self.assertEqual(deprecated["count"], 84)
        self.assertFalse({item["url"] for item in deprecated["cases"]} & {case.url for case in cases})

    def test_direct_media_cases_do_not_use_webpage_content_score(self) -> None:
        _, cases = load_dataset(Path("datasets/file_media_capabilities.json"))
        by_id = {case.id: case for case in cases}
        self.assertFalse(by_id["direct-image"].content_score_applicable)
        self.assertFalse(by_id["direct-video"].content_score_applicable)
        self.assertTrue(by_id["embedded-image-page"].content_score_applicable)


if __name__ == "__main__":
    unittest.main()
