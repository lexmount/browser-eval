import unittest

from scripts.revise_reference_evidence import (
    equivalent_source_verification, reference_fields_for_case,
)


class ReviseReferenceEvidenceTests(unittest.TestCase):
    def test_capture_instance_changes_do_not_expand_reference_revision_scope(self):
        old={"state":"valid","method":"independent","selector":"main",
             "reference_sha256":"a"*64,"checked_at":"2026-09-12T00:00:00Z",
             "http_status":200,"source_snapshot_sha256":"b"*64,
             "rendered_content_sha256":"d"*64,
             "browser_observation_sha256":"e"*64}
        new={**old,"checked_at":"2026-09-14T00:00:00Z",
             "source_snapshot_sha256":"c"*64,
             "browser_observation_sha256":"f"*64}
        self.assertTrue(equivalent_source_verification(old,new))

    def test_reference_state_or_hash_change_remains_in_revision_scope(self):
        old={"state":"unavailable","method":"independent",
             "reference_sha256":None,"checked_at":"2026-09-12T00:00:00Z"}
        recovered={"state":"valid","method":"independent",
                   "reference_sha256":"a"*64,"checked_at":"2026-09-14T00:00:00Z"}
        self.assertFalse(equivalent_source_verification(old,recovered))

    def test_scoring_only_body_change_is_not_imported_as_reference_change(self):
        old={"ground_truth_main_content":"same","source_verification":{},
             "body_requirements":{"version":3}}
        new={"ground_truth_main_content":"same","source_verification":{}}
        fields=reference_fields_for_case(old,new)
        self.assertNotIn("body_requirements",fields)

    def test_body_regions_follow_a_real_reference_change(self):
        old={"ground_truth_main_content":"old","source_verification":{},
             "body_requirements":{"version":3}}
        new={"ground_truth_main_content":"new","source_verification":{}}
        self.assertIn("body_requirements",reference_fields_for_case(old,new))


if __name__ == "__main__":
    unittest.main()
