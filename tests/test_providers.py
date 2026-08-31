import unittest
from unittest.mock import patch

from browser_harness_eval.models import EvalCase
from browser_harness_eval.providers.firecrawl import FirecrawlProvider
from browser_harness_eval.providers.http_client import HttpResponse


class ProviderTest(unittest.TestCase):
    @patch("browser_harness_eval.providers.firecrawl.request")
    def test_firecrawl_rate_limit_is_actionable(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            status=429,
            url="https://api.firecrawl.dev/v2/scrape",
            headers={"content-type": "application/json"},
            body=b'{"success":false,"error":"Keyless rate limit","reason":"credits","retry_after_seconds":120}',
        )
        provider = FirecrawlProvider("firecrawl", {"type": "firecrawl"})
        case = EvalCase(
            id="case",
            name="Case",
            url="https://example.com",
            category="static",
            expected_any=["Example"],
        )
        result = provider.fetch(case, 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "rate_limit")
        self.assertEqual(result.metadata["retry_after_seconds"], 120)


if __name__ == "__main__":
    unittest.main()
