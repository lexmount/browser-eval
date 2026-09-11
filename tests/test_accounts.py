import unittest
from unittest.mock import patch

from harness.web_fetch.accounts import (
    ACCOUNT_REGISTRY, REPORT_LOCAL_ACCESS, REPORT_PROVIDER_ACCOUNT_IDS, env_template,
    report_access_registry,
)


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

    def test_report_registry_is_exactly_the_measured_methods(self) -> None:
        providers = {
            "cloudflare-markdown", "cowagent-built-in", "crawl4ai",
            "deepseek-harness-built-in", "exa", "firecrawl",
            "langchain-webbase", "lexmount-cn", "lexmount-global",
            "moli-webfetch", "openclaw-built-in", "tavily",
        }
        rows = report_access_registry(providers)
        self.assertEqual(len(rows), len(providers))
        names = {row["provider"] for row in rows}
        self.assertNotIn("Browserbase Fetch", names)
        self.assertIn("Moli WebFetch", names)

    def test_report_registry_fails_closed_for_unknown_method(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown-method"):
            report_access_registry({"unknown-method"})

    def test_local_source_methods_document_their_runtime_commands(self) -> None:
        self.assertEqual(
            REPORT_LOCAL_ACCESS["openclaw-built-in"]["env_vars"],
            ["OPENCLAW_REPO", "OPENCLAW_NODE", "OPENCLAW_PNPM"],
        )
        self.assertEqual(
            REPORT_LOCAL_ACCESS["deepseek-harness-built-in"]["env_vars"],
            ["DEEPSEEK_HARNESS_REPO", "OPENCLAW_NODE", "DEEPSEEK_PNPM"],
        )

    def test_report_registry_names_stale_account_mapping(self) -> None:
        with patch.dict(REPORT_PROVIDER_ACCOUNT_IDS, {"stale-method": "missing-account"}):
            with self.assertRaisesRegex(ValueError, "stale-method"):
                report_access_registry({"stale-method"})


if __name__ == "__main__":
    unittest.main()
