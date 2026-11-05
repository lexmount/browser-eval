"""Deterministic controls using the deployment's actual pinned extractor SDK."""

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(
    os.environ.get("BROWSER_EVAL_TEST_NATIVE_SDKS") == "1", "Run make test-extraction-sdk"
)
class NativeExtractionProtocolTests(unittest.TestCase):
    def extract(self, records, *, empty=False):
        import crawl4ai
        import crawl4ai.extraction_strategy as sdk

        from harness.web_extract.self_hosted import crawl

        schema = {
            "type": "object",
            "properties": {
                "contact_names": {"type": "array", "items": {"type": "string"}},
                "capacity": {"type": "string"},
            },
        }

        def completion(*args, **kwargs):
            # The synthetic provider honors JSON mode and preserves the same
            # semantic records in either of the SDK's two supported formats.
            content = (
                json.dumps({"blocks": records, "score": 5})
                if kwargs["json_response"]
                else "<blocks>" + json.dumps(records) + "</blocks><score>5</score>"
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=None if empty else content),
                        finish_reason="length" if empty else "stop",
                    )
                ],
                usage=SimpleNamespace(
                    completion_tokens=8192 if empty else 12,
                    prompt_tokens=10,
                    total_tokens=8202 if empty else 22,
                    completion_tokens_details=None,
                    prompt_tokens_details=None,
                ),
            )

        class Page:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def arun(self, url, config):
                # Exercise the real SDK prompt construction and output parser;
                # only the document transport and model endpoint are replaced.
                result = config.extraction_strategy.extract(url, 0, "A synthetic contact page")
                return SimpleNamespace(success=True, extracted_content=json.dumps(result))

        with (
            patch.dict(
                os.environ,
                {
                    "EXTRACTION_MODEL": "synthetic",
                    "EXTRACTION_MODEL_API_KEY": "synthetic",
                    "EXTRACTION_MODEL_BASE": "https://model.example",
                },
            ),
            patch.object(crawl4ai, "AsyncWebCrawler", side_effect=lambda **kwargs: Page()),
            patch.object(sdk, "perform_completion_with_backoff", side_effect=completion),
        ):
            return asyncio.run(crawl({"url": "https://snapshot.example/frozen", "schema": schema}))

    def test_records_survive_native_prompt_and_output_protocol(self):
        record = {"contact_names": ["Ada", "Grace"], "capacity": "25 seats"}
        data, native = self.extract([record])
        self.assertEqual(data, record)
        self.assertEqual(native["extracted_content"][0]["contact_names"], record["contact_names"])

    def test_multiple_records_are_not_silently_selected(self):
        data, native = self.extract([{"capacity": "10 seats"}, {"capacity": "25 seats"}])
        self.assertIsNone(data)
        self.assertEqual(len(native["extracted_content"]), 2)

    def test_optional_fields_are_not_fabricated(self):
        data, _ = self.extract([{"capacity": "25 seats"}])
        self.assertEqual(data, {"capacity": "25 seats"})

    def test_output_limit_keeps_native_error_and_token_receipt(self):
        data, native = self.extract([], empty=True)
        self.assertIn(data, (None, {}))
        self.assertIn("finish_reason: length", native["extracted_content"][0]["content"])
        self.assertEqual(native["usage"]["completion_tokens"], 8192)
