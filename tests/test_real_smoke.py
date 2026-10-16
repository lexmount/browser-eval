"""Credentialed network smoke tests.

These tests are intentionally opt-in because they make real supplier calls.
Run them through scripts/run_real_smoke.sh; normal unit tests skip this class.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path

from harness.web_fetch.config import load_runtime_env, load_provider_config
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers import build_provider


@unittest.skipUnless(os.getenv("WEBFETCH_REAL_SMOKE") == "1", "set WEBFETCH_REAL_SMOKE=1 for credentialed network calls")
class RealProviderSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env_file = os.getenv("WEBFETCH_SMOKE_ENV_FILE")
        load_runtime_env(Path(env_file) if env_file else None)
        cls.configs = load_provider_config(Path("configs/providers.example.json"))
        cls.case = EvalCase(
            id="real-smoke-example",
            name="Real smoke: Example Domain",
            url="https://example.com/",
            category="static_html",
            expected_any=["This domain is for use in documentation examples"],
        )

    def assert_provider_smoke(self, name: str, case: EvalCase | None = None):
        provider = build_provider(name, self.configs[name])
        missing = provider.validate()
        self.assertFalse(missing, f"{name} missing configuration: {', '.join(missing)}")
        result = provider.fetch(case or self.case, timeout=20)
        self.assertTrue(result.ok, f"{name}: {result.error_type}: {result.error_message}")
        self.assertIn("this domain is for use in documentation examples", result.content.casefold())
        return result

    def test_cowagent_built_in_real_fetch(self) -> None:
        self.assert_provider_smoke("cowagent-built-in")

    def test_moli_real_fetch(self) -> None:
        self.assert_provider_smoke("moli-webfetch")

    def test_moli_same_call_page_identity(self) -> None:
        case = replace(self.case, source_benchmark={"risk_control": {
            "navigation_contract": {
                "requested_url": self.case.url,
                "observed_final_url": self.case.url,
            },
        }})
        result = self.assert_provider_smoke("moli-webfetch", case)
        self.assertEqual(result.metadata["route"], "ordinary_markdown")
        self.assertEqual(result.final_url, case.url)

    def test_lexmount_global_real_fetch(self) -> None:
        self.assert_provider_smoke("lexmount-global")

    def test_lexmount_cn_real_fetch(self) -> None:
        self.assert_provider_smoke("lexmount-cn")

    def test_firecrawl_real_fetch(self) -> None:
        self.assert_provider_smoke("firecrawl")

    def test_tavily_real_fetch(self) -> None:
        self.assert_provider_smoke("tavily")

    def test_exa_real_fetch(self) -> None:
        self.assert_provider_smoke("exa")

    def test_cloudflare_markdown_real_fetch(self) -> None:
        self.assert_provider_smoke("cloudflare-markdown")

    def test_openclaw_built_in_real_fetch(self) -> None:
        self.assert_provider_smoke("openclaw-built-in")

    def test_deepseek_harness_built_in_real_fetch(self) -> None:
        self.assert_provider_smoke("deepseek-harness-built-in")

    def test_langchain_webbase_real_fetch(self) -> None:
        self.assert_provider_smoke("langchain-webbase")

    def test_crawl4ai_real_fetch(self) -> None:
        self.assert_provider_smoke("crawl4ai")

if __name__ == "__main__":
    unittest.main()
