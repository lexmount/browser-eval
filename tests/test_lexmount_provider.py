import unittest

from harness.web_fetch.providers import build_provider


class LexmountProviderTests(unittest.TestCase):
    def test_timeout_cleanup_grace_does_not_extend_answer_deadline(self):
        provider = build_provider(
            "lexmount-cn",
            {"type": "lexmount_cn", "api_key": "key", "project_id": "project"},
        )
        self.assertEqual(provider.cancellation_grace_seconds, 5.0)


if __name__ == "__main__":
    unittest.main()
