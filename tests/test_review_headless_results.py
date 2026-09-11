import unittest

from scripts.review_headless_results import BLINDED_ATTEMPT_KEYS, independent_attempt


class ReviewHeadlessResultsTests(unittest.TestCase):
    def test_blinded_attempt_keeps_request_evidence_but_hides_machine_judgment(self):
        row = {
            "attempt_id": "attempt-1",
            "provider": "provider-1",
            "artifact": "/evidence/artifact.txt",
            "request_ok": True,
            "status_code": 200,
            "final_url": "https://example.com/final",
            "upstream_status": 200,
            "error_type": None,
            "returned_links": [{"text": "Example", "href": "https://example.com/item"}],
            "machine_verdict": "pass",
            "metrics": {"stable_anchor_hits": 3},
        }

        packet = independent_attempt(row)

        self.assertEqual(packet["status_code"], 200)
        self.assertEqual(packet["final_url"], "https://example.com/final")
        self.assertEqual(packet["upstream_status"], 200)
        self.assertEqual(packet["returned_links"][0]["text"], "Example")
        self.assertNotIn("machine_verdict", packet)
        self.assertNotIn("metrics", packet)
        self.assertLessEqual(set(packet), set(BLINDED_ATTEMPT_KEYS))


if __name__ == "__main__":
    unittest.main()
