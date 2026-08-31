"""Credentialed network smoke tests.

These tests are intentionally opt-in because they make real supplier calls.
Run them through scripts/run_real_smoke.sh; normal unit tests skip this class.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from browser_harness_eval.config import load_env_file, load_provider_config
from browser_harness_eval.models import EvalCase
from browser_harness_eval.providers import build_provider


@unittest.skipUnless(os.getenv("WEBFETCH_REAL_SMOKE") == "1", "set WEBFETCH_REAL_SMOKE=1 for credentialed network calls")
class RealProviderSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        env_file = Path(os.getenv("WEBFETCH_SMOKE_ENV_FILE", "~/.env")).expanduser()
        if env_file.exists():
            load_env_file(env_file)
        cls.configs = load_provider_config(Path("configs/providers.example.json"))
        cls.case = EvalCase(
            id="real-smoke-example",
            name="Real smoke: Example Domain",
            url="https://example.com/",
            category="static_html",
            expected_any=["Example Domain"],
        )

    def assert_provider_smoke(self, name: str) -> None:
        provider = build_provider(name, self.configs[name])
        missing = provider.validate()
        self.assertFalse(missing, f"{name} missing configuration: {', '.join(missing)}")
        result = provider.fetch(self.case, timeout=20)
        self.assertTrue(result.ok, f"{name}: {result.error_type}: {result.error_message}")
        self.assertIn("example domain", result.content.casefold())

    def test_cowagent_built_in_real_fetch(self) -> None:
        self.assert_provider_smoke("cowagent-built-in")

    def test_lexmount_global_real_fetch(self) -> None:
        self.assert_provider_smoke("lexmount-global")

    def test_firecrawl_real_fetch(self) -> None:
        self.assert_provider_smoke("firecrawl")

    def test_tavily_real_fetch(self) -> None:
        self.assert_provider_smoke("tavily")

    def test_exa_real_fetch(self) -> None:
        self.assert_provider_smoke("exa")

    def test_cloudflare_markdown_real_fetch(self) -> None:
        self.assert_provider_smoke("cloudflare-markdown")

    def test_cloudflare_content_real_fetch(self) -> None:
        self.assert_provider_smoke("cloudflare-content")


if __name__ == "__main__":
    unittest.main()
