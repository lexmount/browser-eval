import unittest
from unittest.mock import patch

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.firecrawl import FirecrawlProvider
from harness.web_fetch.providers.cloudflare import CloudflareContentProvider, CloudflareMarkdownProvider
from harness.web_fetch.providers.exa import ExaContentsProvider
from harness.web_fetch.providers.tavily import TavilyExtractProvider
from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider
from harness.web_fetch.providers.http_client import HttpResponse


class ProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.case = EvalCase(
            id="case", name="Case", url="https://example.com",
            category="static", expected_any=["Example"],
        )

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_rate_limit_is_actionable(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            status=429,
            url="https://api.firecrawl.dev/v2/scrape",
            headers={"content-type": "application/json"},
            body=b'{"success":false,"error":"Keyless rate limit","reason":"credits","retry_after_seconds":120}',
        )
        provider = FirecrawlProvider("firecrawl", {"type": "firecrawl"})
        result = provider.fetch(self.case, 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "rate_limit")
        self.assertEqual(result.metadata["retry_after_seconds"], 120)

    @patch("harness.web_fetch.providers.tavily.request")
    def test_tavily_extract_reads_markdown(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"results":[{"url":"https://example.com","raw_content":"# Example"}]}')
        result = TavilyExtractProvider("tavily", {"api_key": "x"}).fetch(self.case, 1)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "# Example")

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_contents_reads_text(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"results":[{"url":"https://example.com","text":"# Example"}],"statuses":[{"status":"success"}]}')
        result = ExaContentsProvider("exa", {"api_key": "x"}).fetch(self.case, 1)
        self.assertTrue(result.ok)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_reads_markdown(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"# Example","meta":{"status":200}}')
        result = CloudflareMarkdownProvider("cf", {"api_token": "x", "account_id": "a"}).fetch(self.case, 1)
        self.assertTrue(result.ok)

    def test_provider_exposes_plan_rate_interval(self) -> None:
        provider = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a", "min_interval_seconds": 10.5}
        )
        self.assertEqual(provider.min_interval_seconds, 10.5)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_rate_limit_is_classified(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            429, "u", {"retry-after": "10"}, b'{"success":false,"errors":[{"message":"Too many requests"}]}'
        )
        result = CloudflareMarkdownProvider("cf", {"api_token": "x", "account_id": "a"}).fetch(self.case, 1)
        self.assertEqual(result.error_type, "rate_limit")
        self.assertEqual(result.metadata["retry_after"], "10")

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_content_reads_rendered_html(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":"<html>Example</html>"}')
        result = CloudflareContentProvider("cf-content", {"api_token": "x", "account_id": "a"}).fetch(self.case, 1)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["output_contract"], "rendered_html")

    def test_cowagent_requires_real_checkout(self) -> None:
        provider = CowAgentBuiltinProvider("cowagent", {"repo_path": "/definitely/missing"})
        self.assertTrue(provider.validate())


if __name__ == "__main__":
    unittest.main()
