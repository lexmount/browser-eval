import unittest

from browser_harness_eval.accounts import ACCOUNT_REGISTRY, env_template


class AccountRegistryTest(unittest.TestCase):
    def test_registry_has_official_links_and_unique_ids(self) -> None:
        ids = [item["id"] for item in ACCOUNT_REGISTRY]
        self.assertEqual(len(ids), len(set(ids)))
        for item in ACCOUNT_REGISTRY:
            self.assertTrue(item["registration_url"].startswith("https://"))
            self.assertTrue(item["key_url"].startswith("https://"))
            self.assertTrue(item["env_vars"])

    def test_env_template_contains_core_keys_without_values(self) -> None:
        template = env_template()
        self.assertIn("LEXMOUNT_API_KEY=\n", template)
        self.assertIn("FIRECRAWL_API_KEY=\n", template)
        self.assertNotIn("secret", template.casefold())


if __name__ == "__main__":
    unittest.main()
