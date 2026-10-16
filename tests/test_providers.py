import base64
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.metrics import evaluate_resources
from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.cloudflare import (
    CloudflareContentProvider,
    CloudflareMarkdownProvider,
    _post_multipart,
)
from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider, _sanitize_public_content
from harness.web_fetch.providers.exa import ExaContentsProvider
from harness.web_fetch.providers.firecrawl import FirecrawlProvider
from harness.web_fetch.providers.http import HttpProvider
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch.providers.lexmount import LexmountProvider
from harness.web_fetch.providers.tavily import TavilyExtractProvider
from harness.web_fetch.publication_privacy import project_file
from harness.web_fetch.runner import run_evaluation


class ProviderTest(unittest.TestCase):
    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_native_page_scope_is_explicit_and_preserves_answer(self, mock_request):
        returned = "Header\n\nMain text\n\nFooter"
        mock_request.return_value = HttpResponse(
            200, "https://api.firecrawl.dev/v2/scrape", {},
            json.dumps({"success": True, "data": {"markdown": returned}}).encode(),
        )
        case = EvalCase(id="scope", name="Scope", url="https://example.com", category="static", expected_any=[])
        for scope in (None, False, True):
            config = {"api_key": "x", "uniform_native_parameters": True}
            if scope is not None:
                config["only_main_content"] = scope
            result = FirecrawlProvider("firecrawl", config).fetch(case, 45)
            body = mock_request.call_args.kwargs["json_body"]
            self.assertEqual(result.content, returned)
            self.assertEqual(body.get("onlyMainContent"), scope)
            self.assertEqual("onlyMainContent" in body, scope is not None)
            self.assertNotIn("actions", body)
            self.assertNotIn("waitFor", body)
        self.assertEqual(
            FirecrawlProvider("firecrawl", {"api_key": "x", "only_main_content": "false"}).validate(),
            ["only_main_content must be boolean"],
        )

    def setUp(self) -> None:
        self.case = EvalCase(
            id="case", name="Case", url="https://example.com",
            category="static", expected_any=["Example"],
        )

    @patch("harness.web_fetch.providers.lexmount.request")
    def test_lexmount_engine_uses_nested_extract_options(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            b'{"result":{"main_text":"Example","engine":"lightmount_dcl"}}',
        )

        result = LexmountProvider(
            "lexmount",
            {"api_key": "x", "project_id": "p", "engine": "lightmount_dcl"},
        ).fetch(self.case, 45)

        self.assertTrue(result.ok)
        self.assertEqual(mock_request.call_args.kwargs["json_body"], {
            "extract": {
                "url": "https://example.com",
                "options": {"engine_preference": "lightmount_dcl"},
            }
        })
        self.assertNotIn("engine", mock_request.call_args.kwargs["json_body"]["extract"])

    @patch("harness.web_fetch.providers.lexmount.request")
    def test_lexmount_never_uses_api_envelope_as_page_content(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            b'{"result":{"main_text":"","title":"Envelope only"}}',
        )

        result = LexmountProvider(
            "lexmount", {"api_key": "x", "project_id": "p"}
        ).fetch(self.case, 45)

        self.assertTrue(result.ok)
        self.assertEqual(result.content, "")
        self.assertNotIn("Envelope only", result.content)

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_rate_limit_is_actionable(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            status=429,
            url="https://api.firecrawl.dev/v2/scrape",
            headers={"content-type": "application/json"},
            body=(
                b'{"success":false,"error":"Keyless rate limit","reason":"credits",'
                b'"retry_after_seconds":120}'
            ),
        )
        provider = FirecrawlProvider("firecrawl", {"type": "firecrawl"})
        result = provider.fetch(self.case, 45)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "rate_limit")
        self.assertEqual(result.metadata["retry_after_seconds"], 120)
        self.assertEqual(result.metadata["requested_formats"], ["markdown"])
        self.assertEqual(result.metadata["requested_proxy"], "auto")
        self.assertIsNone(result.metadata["requested_dynamic_wait_strategy"])
        self.assertEqual(result.metadata["requested_cache_max_age_ms"], 0)
        self.assertIsNone(result.metadata["requested_parsers"])

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_insufficient_credits_are_account_capacity_and_private(
        self, mock_request
    ) -> None:
        secret = "private-account-detail"
        body = json.dumps({
            "success": False,
            "error": (
                "Insufficient credits to perform this request. "
                "For more credits, upgrade your plan. " + secret
            ),
            "reason": "credits",
        }).encode()
        mock_request.return_value = HttpResponse(
            status=402,
            url="https://api.firecrawl.dev/v2/scrape",
            headers={"content-type": "application/json"},
            body=body,
        )

        result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(self.case, 45)

        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "account_capacity")
        self.assertEqual(
            result.error_message, "Firecrawl account has insufficient credits"
        )
        self.assertEqual(
            result.metadata["account_capacity_reason"], "insufficient_credits"
        )
        public_metadata = {
            key: value for key, value in result.metadata.items()
            if key != "_raw_response_b64"
        }
        self.assertNotIn(secret, result.error_message)
        self.assertNotIn(secret, json.dumps(public_metadata))
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_other_payment_required_is_still_account_capacity(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            status=402,
            url="https://api.firecrawl.dev/v2/scrape",
            headers={"content-type": "application/json"},
            body=b'{"success":false,"error":"Plan does not permit this request"}',
        )

        result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(self.case, 45)

        self.assertEqual(result.error_type, "account_capacity")
        self.assertEqual(
            result.error_message,
            "Firecrawl account capacity is unavailable (HTTP 402)",
        )
        self.assertEqual(result.metadata["account_capacity_reason"], "payment_required")

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_file_route_returns_target_bound_raw_bytes(self, mock_request) -> None:
        raw = b"\x89PNG\r\n\x1a\n" + b"payload" * 10
        body = json.dumps({
            "success": True,
            "data": {
                "rawBase64": base64.b64encode(raw).decode("ascii"),
                "metadata": {
                    "sourceURL": "https://example.com/file.png",
                    "contentType": "image/png",
                    "statusCode": 200,
                },
            },
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, body)
        case = EvalCase(
            id="file",
            name="File",
            url="https://example.com/file.png",
            category="image",
            expected_any=[],
            task_kind="file",
        )

        result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(case, 45)

        self.assertTrue(result.ok)
        self.assertEqual(result.content, "")
        request_body = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(request_body["formats"], ["rawBase64"])
        self.assertEqual(request_body["parsers"], [])
        self.assertEqual(request_body["proxy"], "auto")
        self.assertEqual(request_body["timeout"], 40_500)
        self.assertEqual(mock_request.call_args.kwargs["timeout"], 40.5)
        self.assertEqual(result.metadata["service_timeout_ms"], 40_500)
        self.assertEqual(result.metadata["client_http_timeout_ms"], 40_500)
        self.assertEqual(result.metadata["requested_formats"], ["rawBase64"])
        self.assertEqual(result.metadata["requested_proxy"], "auto")
        self.assertIsNone(result.metadata["requested_dynamic_wait_strategy"])
        self.assertEqual(result.metadata["requested_cache_max_age_ms"], 0)
        self.assertEqual(result.metadata["requested_parsers"], [])
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.files[0]["data"], raw)
        self.assertEqual(result.files[0]["target_url"], case.url)
        self.assertEqual(result.metadata["route"], "v2_scrape_raw_base64")
        self.assertEqual(result.metadata["raw_file"], {
            "target_url": case.url,
            "source_url": case.url,
            "content_type": "image/png",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_video_track_requests_and_returns_native_videos(
        self, mock_request
    ) -> None:
        videos = [{
            "url": "https://cdn.example.com/movie.mp4",
            "sourceURL": "https://example.com/watch",
            "mimeType": "video/mp4",
        }]
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            json.dumps({"success": True, "data": {"markdown": "", "videos": videos}}).encode(),
        )
        case = EvalCase(
            id="video",
            name="Video",
            url="https://example.com/watch",
            category="video",
            expected_any=[],
            track="video",
            task_kind="resource",
            expected_resources=[{
                "id": "video",
                "kind": "video",
                "urls": ["https://cdn.example.com/movie.mp4"],
                "url_patterns": [],
            }],
        )

        result = FirecrawlProvider(
            "firecrawl", {"api_key": "x", "formats": ["markdown", "links", "images"]}
        ).fetch(case, 45)

        self.assertTrue(result.ok)
        self.assertEqual(
            mock_request.call_args.kwargs["json_body"]["formats"],
            ["markdown", "links", "images", "video"],
        )
        self.assertEqual(result.metadata["requested_formats"][-1], "video")
        self.assertEqual(result.metadata["videos"], videos)
        metrics = evaluate_resources(
            result.content,
            result.metadata,
            case.expected_resources,
            base_url=case.url,
            eligible=result.ok,
        )
        self.assertTrue(metrics["resource_pass"])
        self.assertEqual(
            metrics["resource_results"][0]["matched_urls"],
            ["https://cdn.example.com/movie.mp4"],
        )

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_does_not_request_video_format_for_other_tracks(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200, "u", {}, b'{"success":true,"data":{"markdown":"Example"}}'
        )

        FirecrawlProvider(
            "firecrawl", {"api_key": "x", "formats": ["markdown", "links", "images"]}
        ).fetch(self.case, 45)

        self.assertEqual(
            mock_request.call_args.kwargs["json_body"]["formats"],
            ["markdown", "links", "images"],
        )

        video_body_case = EvalCase(
            id="video-body",
            name="Video transcript",
            url="https://example.com/watch",
            category="video",
            expected_any=["Example"],
            track="video",
            task_kind="body",
        )
        FirecrawlProvider(
            "firecrawl", {"api_key": "x", "formats": ["markdown", "links", "images"]}
        ).fetch(video_body_case, 45)
        self.assertEqual(
            mock_request.call_args.kwargs["json_body"]["formats"],
            ["markdown", "links", "images"],
        )

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_capacity_preflight_uses_conservative_declared_bound(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "https://api.firecrawl.dev/v2/team/credit-usage",
            {},
            b'{"success":true,"data":{"remainingCredits":150,"planCredits":1000}}',
        )
        provider = FirecrawlProvider(
            "firecrawl",
            {
                "api_key": "x",
                "proxy": "auto",
                "capacity_credits_per_call": 1,
            },
        )

        provider.preflight_capacity(150)

        self.assertEqual(
            mock_request.call_args.args[0],
            "https://api.firecrawl.dev/v2/team/credit-usage",
        )
        self.assertEqual(mock_request.call_args.kwargs["method"], "GET")
        self.assertEqual(mock_request.call_args.kwargs["timeout"], 9.0)
        self.assertNotIn("json_body", mock_request.call_args.kwargs)

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_capacity_preflight_uses_exact_mixed_case_credit_bound(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "https://api.firecrawl.dev/v2/team/credit-usage",
            {},
            b'{"success":true,"data":{"remainingCredits":27}}',
        )
        video_resource = EvalCase(
            id="video-resource",
            name="Video resource",
            url="https://example.com/watch",
            category="video",
            expected_any=[],
            track="video",
            task_kind="resource",
        )
        video_body = EvalCase(
            id="video-body",
            name="Video body",
            url="https://example.com/transcript",
            category="video",
            expected_any=[],
            track="video",
            task_kind="body",
        )
        provider = FirecrawlProvider(
            "firecrawl", {"api_key": "x", "capacity_credits_per_call": 1}
        )
        self.assertEqual(
            provider.capacity_credits_for_cases(
                [video_resource, video_body, self.case],
                runs=1,
                max_failure_retries=0,
            ),
            7,
        )
        required = provider.capacity_credits_for_cases(
            [video_resource, video_body, self.case],
            runs=2,
            max_failure_retries=1,
        )

        self.assertEqual(required, 28)
        with self.assertRaisesRegex(ValueError, "requires at least 28 credits"):
            provider.preflight_capacity(
                declared_calls=12,
                required_credits=required,
            )

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_capacity_preflight_fails_before_calls_without_exposing_balance(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "https://api.firecrawl.dev/v2/team/credit-usage",
            {},
            b'{"success":true,"data":{"remainingCredits":0,"planCredits":1000}}',
        )
        provider = FirecrawlProvider(
            "firecrawl",
            {
                "api_key": "x",
                "proxy": "auto",
                "capacity_credits_per_call": 1,
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "account capacity is insufficient.*requires at least 150 credits",
        ) as raised:
            provider.preflight_capacity(150)

        self.assertNotIn("remainingCredits", str(raised.exception))
        self.assertNotIn("planCredits", str(raised.exception))

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_capacity_preflight_rejects_understated_proxy_cost(
        self, mock_request
    ) -> None:
        provider = FirecrawlProvider(
            "firecrawl",
            {
                "api_key": "x",
                "proxy": "auto",
                "capacity_credits_per_call": 0,
            },
        )

        with self.assertRaisesRegex(ValueError, "below the configured proxy bound"):
            provider.preflight_capacity(150)

        mock_request.assert_not_called()

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_api_envelope_and_invalid_file_payload_stay_private(self, mock_request) -> None:
        secret = "Authorization: Bearer private-firecrawl-token"
        body = json.dumps({
            "success": True,
            "data": {"rawBase64": "not base64!", "warning": secret},
            "debug": secret,
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, body)
        case = EvalCase(
            id="file",
            name="File",
            url="https://example.com/file.png",
            category="image",
            expected_any=[],
            task_kind="file",
        )

        result = FirecrawlProvider("firecrawl", {"api_key": "x"}).fetch(case, 45)

        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "response_validation")
        self.assertEqual(result.error_message, "Firecrawl returned invalid rawBase64")
        self.assertEqual(result.files, [])
        public_metadata = {
            key: value for key, value in result.metadata.items()
            if key != "_raw_response_b64"
        }
        self.assertNotIn(secret, json.dumps(public_metadata))
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)

    @patch("harness.web_fetch.providers.firecrawl.request")
    def test_firecrawl_runner_hash_binds_envelope_outside_public_artifacts(
        self, mock_request
    ) -> None:
        raw = b"\x89PNG\r\n\x1a\n" + b"payload" * 10
        secret = "private-firecrawl-envelope"
        body = json.dumps({
            "success": True,
            "data": {
                "rawBase64": base64.b64encode(raw).decode("ascii"),
                "warning": secret,
                "metadata": {"contentType": "image/png"},
            },
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, body)
        case = EvalCase(
            id="file",
            name="File",
            url="https://example.com/file.png",
            category="image",
            expected_any=[],
            task_kind="file",
            source_verification={
                "mime": "image/png",
                "file_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(
                output_dir=root,
                dataset_meta={"id": "firecrawl-file-test"},
                dataset_path=root / "missing-dataset.json",
                cases=[case],
                providers=[FirecrawlProvider("firecrawl", {"api_key": "x"})],
                provider_config_path=root / "missing-providers.json",
                runs=1,
                timeout=45,
                delay=0,
            )
            record = records[0]
            public_results = (root / "results.jsonl").read_bytes()
            private_path = root / record["provider_metadata"]["raw_response_path"]
            delivered = record["provider_metadata"]["file_evaluation"]["files"][0]

            self.assertNotIn(secret.encode(), public_results)
            self.assertEqual(private_path.read_bytes(), body)
            self.assertEqual(
                record["provider_metadata"]["raw_response_sha256"],
                hashlib.sha256(body).hexdigest(),
            )
            self.assertEqual((root / delivered["artifact_path"]).read_bytes(), raw)

    @patch("harness.web_fetch.providers.tavily.request")
    def test_tavily_extract_reads_markdown(self, mock_request) -> None:
        body = b'{"results":[{"url":"https://example.com","raw_content":"# Example"}]}'
        mock_request.return_value = HttpResponse(200, "u", {}, body)
        result = TavilyExtractProvider("tavily", {"api_key": "x"}).fetch(self.case, 45)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "# Example")
        request_body = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(request_body["extract_depth"], "advanced")
        self.assertEqual(request_body["format"], "markdown")
        self.assertFalse(request_body["include_images"])
        self.assertEqual(request_body["timeout"], 40.5)
        self.assertEqual(result.metadata["extract_timeout_seconds"], 40.5)
        self.assertEqual(result.metadata["requested_extract_depth"], "advanced")
        self.assertFalse(result.metadata["requested_include_images"])
        self.assertEqual(result.metadata["requested_format"], "markdown")
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)
        self.assertEqual(result.metadata["raw_response_sha256"], hashlib.sha256(body).hexdigest())
        self.assertNotIn("raw_response", result.metadata)

    @patch("harness.web_fetch.providers.tavily.request")
    def test_tavily_preserves_explicit_depth_and_resource_mode(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"results":[]}')
        provider = TavilyExtractProvider(
            "tavily", {"api_key": "x", "extract_depth": "basic", "include_images": True}
        )
        result = provider.fetch(self.case, 1)
        request_body = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(request_body["extract_depth"], "basic")
        self.assertTrue(request_body["include_images"])
        self.assertEqual(result.metadata["requested_extract_depth"], "basic")
        self.assertTrue(result.metadata["requested_include_images"])
        self.assertEqual(result.metadata["requested_format"], "markdown")

    @patch("harness.web_fetch.providers.tavily.request")
    def test_tavily_service_timeout_is_capped_by_inner_http_budget(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"results":[]}')
        TavilyExtractProvider(
            "tavily", {"api_key": "x", "extract_timeout_seconds": 60.0}
        ).fetch(self.case, 12.5)

        self.assertEqual(mock_request.call_args.kwargs["timeout"], 11.25)
        self.assertEqual(mock_request.call_args.kwargs["json_body"]["timeout"], 11.25)

    @patch("harness.web_fetch.providers.tavily.request")
    def test_tavily_empty_and_api_error_responses_are_private_only(self, mock_request) -> None:
        secret = "Authorization: Bearer private-tavily-token"
        for status, body, error_type, message in (
            (200, b'{"results":[]}', "empty_content", "Tavily returned no extractable content"),
            (500, json.dumps({
                "detail": secret,
                "failed_results": [{"error": secret}],
                "images": [secret],
            }).encode(),
             "api_error", "HTTP 500"),
            (502, secret.encode(), "api_error", "HTTP 502"),
        ):
            with self.subTest(status=status, body=body):
                mock_request.return_value = HttpResponse(status, "u", {}, body)
                result = TavilyExtractProvider("tavily", {"api_key": "x"}).fetch(self.case, 2)
                self.assertFalse(result.ok)
                self.assertEqual(result.content, "")
                self.assertEqual(result.error_type, error_type)
                self.assertEqual(result.error_message, message)
                public_metadata = {
                    key: value for key, value in result.metadata.items()
                    if key != "_raw_response_b64"
                }
                self.assertNotIn(secret, json.dumps(public_metadata))
                self.assertNotIn("failed_results", public_metadata)
                self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)

    @patch("harness.web_fetch.providers.tavily.request")
    def test_tavily_projects_only_allowlisted_failed_result_errors(self, mock_request) -> None:
        secret = "Authorization: Bearer private-tavily-token"
        body = json.dumps({
            "results": [],
            "failed_results": [
                {"url": self.case.url, "error": "Failed to fetch url"},
                {"url": self.case.url, "error": "404 page not found"},
                {"url": self.case.url, "error": secret},
                {"url": self.case.url, "error": {"message": "not a string"}},
            ],
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, body)

        result = TavilyExtractProvider("tavily", {"api_key": "x"}).fetch(self.case, 2)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["failed_result_count"], 4)
        self.assertEqual(
            result.metadata["failed_result_errors"],
            ["Failed to fetch url", "404 page not found"],
        )
        self.assertNotIn(secret, json.dumps({
            key: value for key, value in result.metadata.items()
            if key != "_raw_response_b64"
        }))
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_contents_reads_text(self, mock_request) -> None:
        body = (
            b'{"results":[{"url":"https://example.com","text":"# Example"}],'
            b'"statuses":[{"status":"success"}]}'
        )
        mock_request.return_value = HttpResponse(200, "u", {}, body)
        result = ExaContentsProvider("exa", {"api_key": "x"}).fetch(self.case, 45)
        self.assertTrue(result.ok)
        request_body = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(request_body["text"], {"verbosity": "full"})
        self.assertEqual(request_body["maxAgeHours"], 0)
        self.assertEqual(request_body["livecrawlTimeout"], 15_000)
        self.assertEqual(result.metadata["livecrawl_timeout_ms"], 15_000)
        self.assertNotIn("extras", request_body)
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)
        self.assertEqual(result.metadata["raw_response_sha256"], hashlib.sha256(body).hexdigest())
        self.assertNotIn("raw_response", result.metadata)

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_preserves_freshness_and_resource_fields(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"results":[]}')
        provider = ExaContentsProvider(
            "exa", {"api_key": "x", "max_age_hours": 12, "include_resource_links": True}
        )
        provider.fetch(self.case, 1)
        request_body = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(request_body["text"], {"verbosity": "full"})
        self.assertEqual(request_body["maxAgeHours"], 12)
        self.assertEqual(request_body["extras"], {"links": 20, "imageLinks": 20})

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_named_link_uses_explicit_rich_pairs(self, mock_request) -> None:
        case = EvalCase(
            id="list", name="List", url="https://example.com/catalog",
            category="challenge", expected_any=[], task_kind="structure",
            acceptance_contract={
                "version": 1, "schema": "named_link", "minimum_records": 3,
                "hosts": ["example.com"], "entry_path": r"/item/\d+",
            },
        )
        body = json.dumps({
            "results": [{
                "url": case.url,
                "text": "Alpha Beta Gamma",
                "extras": {
                    "links": ["/item/1", "/item/2"],
                    "richLinks": [
                        {"url": "/item/1", "anchor": "Alpha"},
                        {"url": "/item/2", "anchor": ""},
                        {"url": "/item/3"},
                        {"url": "/item/4", "anchor": "Delta"},
                    ],
                },
            }],
            "statuses": [{"status": "success"}],
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, body)

        result = ExaContentsProvider("exa", {"api_key": "x"}).fetch(case, 45)

        request_body = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(request_body["extras"], {"richLinks": 100})
        self.assertEqual(result.metadata["links"], [
            {"text": "Alpha", "href": "/item/1"},
            {"text": "Delta", "href": "/item/4"},
        ])
        self.assertEqual(result.metadata["extras"]["links"], ["/item/1", "/item/2"])

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_livecrawl_timeout_is_capped_by_inner_http_budget(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"results":[]}')
        ExaContentsProvider(
            "exa", {"api_key": "x", "livecrawl_timeout_ms": 30_000}
        ).fetch(self.case, 12.5)

        self.assertEqual(mock_request.call_args.kwargs["timeout"], 11.25)
        self.assertEqual(
            mock_request.call_args.kwargs["json_body"]["livecrawlTimeout"], 11_250
        )

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_empty_status_and_api_error_responses_are_private_only(self, mock_request) -> None:
        secret = "Authorization: Bearer private-exa-token"
        for status, body, error_type, message in (
            (200, b'{"results":[],"statuses":[{"status":"success"}]}',
             "empty_content", "Exa returned no extractable text"),
            (200, json.dumps({
                "results": [{"extras": {"links": [secret]}}],
                "statuses": [{"status": "error", "error": {"tag": secret}}],
            }).encode(),
             "api_error", "Exa reported an unsuccessful URL status"),
            (500, json.dumps({"error": secret}).encode(), "api_error", "HTTP 500"),
            (502, secret.encode(), "api_error", "HTTP 502"),
        ):
            with self.subTest(status=status, body=body):
                mock_request.return_value = HttpResponse(status, "u", {}, body)
                result = ExaContentsProvider("exa", {"api_key": "x"}).fetch(self.case, 2)
                self.assertFalse(result.ok)
                self.assertEqual(result.content, "")
                self.assertEqual(result.error_type, error_type)
                self.assertEqual(result.error_message, message)
                public_metadata = {
                    key: value for key, value in result.metadata.items()
                    if key != "_raw_response_b64"
                }
                self.assertNotIn(secret, json.dumps(public_metadata))
                self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_projects_only_allowlisted_per_url_error_fields(self, mock_request) -> None:
        body = json.dumps({
            "results": [],
            "statuses": [{
                "id": self.case.url,
                "status": "error",
                "error": {
                    "tag": "CRAWL_LIVECRAWL_TIMEOUT",
                    "httpStatusCode": 504,
                    "message": "private response body",
                },
            }],
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, body)

        result = ExaContentsProvider("exa", {"api_key": "x"}).fetch(self.case, 2)

        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["url_status"], "error")
        self.assertEqual(result.metadata["url_error"], {
            "tag": "CRAWL_LIVECRAWL_TIMEOUT",
            "httpStatusCode": 504,
        })
        self.assertNotIn("private response body", json.dumps({
            key: value for key, value in result.metadata.items()
            if key != "_raw_response_b64"
        }))

        unsafe_body = json.dumps({
            "results": [],
            "statuses": [{
                "status": "error",
                "error": {
                    "tag": "Authorization: Bearer private-exa-token",
                    "httpStatusCode": 700,
                },
            }],
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, unsafe_body)

        unsafe = ExaContentsProvider("exa", {"api_key": "x"}).fetch(self.case, 2)

        self.assertEqual(unsafe.metadata["url_error"], {})
        self.assertNotIn("private-exa-token", json.dumps({
            key: value for key, value in unsafe.metadata.items()
            if key != "_raw_response_b64"
        }))

    @patch("harness.web_fetch.providers.exa.request")
    def test_exa_retains_declared_cost_total_without_changing_answer(self, mock_request):
        for cost, expected in (({"total": 0.003, "contents": {"text": 0.003}}, 0.003),
                               (0.002, 0.002), ({"total": True}, None), ({}, None)):
            with self.subTest(cost=cost):
                payload = {"results": [{"url": self.case.url, "text": "Native body"}],
                           "statuses": [{"status": "success"}], "costDollars": cost}
                mock_request.return_value = HttpResponse(200, self.case.url, {}, json.dumps(payload).encode())
                result = ExaContentsProvider("exa", {"api_key": "x"}).fetch(self.case, 45)
                self.assertEqual(result.metadata["cost_dollars"], expected)
                self.assertTrue(result.ok)
                self.assertEqual(result.content, "Native body")

    def test_all_synchronous_http_adapters_reserve_outer_deadline_budget(self) -> None:
        providers = (
            ("harness.web_fetch.providers.http.request", HttpProvider("http", {})),
            ("harness.web_fetch.providers.cloudflare.request",
             CloudflareMarkdownProvider("cloudflare", {"api_token": "x", "account_id": "a"})),
            ("harness.web_fetch.providers.cloudflare.request",
             CloudflareContentProvider(
                 "cloudflare-content", {"api_token": "x", "account_id": "a"}
             )),
            ("harness.web_fetch.providers.firecrawl.request",
             FirecrawlProvider("firecrawl", {"base_url": "https://localhost"})),
            ("harness.web_fetch.providers.lexmount.request",
             LexmountProvider("lexmount", {"api_key": "x", "project_id": "p"})),
            ("harness.web_fetch.providers.exa.request",
             ExaContentsProvider("exa", {"api_key": "x"})),
            ("harness.web_fetch.providers.tavily.request",
             TavilyExtractProvider("tavily", {"api_key": "x"})),
        )
        for request_target, provider in providers:
            with self.subTest(provider=provider.name), patch(request_target) as mock_request:
                mock_request.return_value = HttpResponse(500, "u", {}, b"{}")
                provider.fetch(self.case, 45)
                self.assertEqual(mock_request.call_args.kwargs["timeout"], 40.5)

    @patch("harness.web_fetch.providers.exa.request")
    def test_inner_http_timeout_finishes_before_deadline_and_next_case_runs(
        self, mock_request
    ) -> None:
        seen_timeouts = []

        def time_out(*_args, **kwargs):
            seen_timeouts.append(kwargs["timeout"])
            time.sleep(kwargs["timeout"])
            raise TimeoutError("inner HTTP timeout")

        mock_request.side_effect = time_out
        cases = [
            EvalCase(f"case-{index}", f"Case {index}", f"https://example.com/{index}",
                     "static", ["Expected"])
            for index in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(
                output_dir=root,
                dataset_meta={"id": "timeout-test"},
                dataset_path=root / "missing-dataset.json",
                cases=cases,
                providers=[ExaContentsProvider("exa", {"api_key": "x"})],
                provider_config_path=root / "missing-providers.json",
                runs=1,
                timeout=0.4,
                delay=0,
            )
            manifest = json.loads((root / "manifest.json").read_text())
        self.assertEqual(seen_timeouts, [0.2, 0.2, 0.2, 0.2])
        self.assertEqual(len(records), 2)
        self.assertEqual([record["error_type"] for record in records], ["timeout", "timeout"])
        self.assertEqual(manifest["unexecuted"], [])

    @patch("harness.web_fetch.providers.exa.request")
    def test_failed_api_envelope_cannot_enter_public_projection(self, mock_request) -> None:
        secret = b"Authorization: Bearer private-provider-token"
        body = json.dumps({"error": secret.decode()}).encode()
        mock_request.return_value = HttpResponse(500, "u", {}, body)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(
                output_dir=root,
                dataset_meta={"id": "private-response-test"},
                dataset_path=root / "missing-dataset.json",
                cases=[self.case],
                providers=[ExaContentsProvider("exa", {"api_key": "x"})],
                provider_config_path=root / "missing-providers.json",
                runs=1,
                timeout=2,
                delay=0,
            )
            record = records[0]
            public_artifact = (root / record["artifact_path"]).read_bytes()
            public_results = (root / "results.jsonl").read_bytes()
            private_path = root / record["provider_metadata"]["raw_response_path"]
            self.assertEqual(public_artifact, b"")
            self.assertNotIn(secret, public_results)
            self.assertNotIn(base64.b64encode(body), public_results)
            self.assertEqual(private_path.read_bytes(), body)
            self.assertEqual(project_file(record["artifact_path"], public_artifact), (b"", None))
            self.assertNotIn(secret, project_file("results.jsonl", public_results)[0])

    def test_release_configs_bind_strongest_tavily_and_exa_text_routes(self) -> None:
        for filename in ("configs/providers.example.json", "configs/providers.resources.json"):
            providers = json.loads(Path(filename).read_text())["providers"]
            with self.subTest(filename=filename):
                self.assertEqual(providers["tavily"]["extract_depth"], "advanced")
                self.assertEqual(providers["tavily-resources"]["extract_depth"], "advanced")
                self.assertEqual(providers["tavily"]["extract_timeout_seconds"], 60.0)
                self.assertEqual(providers["tavily-resources"]["extract_timeout_seconds"], 60.0)
                self.assertEqual(providers["exa"]["text_verbosity"], "full")
                self.assertEqual(providers["exa-resources"]["text_verbosity"], "full")
                self.assertEqual(providers["exa"]["livecrawl_timeout_ms"], 15_000)
                self.assertEqual(providers["exa-resources"]["livecrawl_timeout_ms"], 15_000)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_reads_markdown(self, mock_request) -> None:
        body = b'{"success":true,"result":"# Example","meta":{"status":200}}'
        mock_request.return_value = HttpResponse(200, "u", {}, body)
        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(self.case, 45)
        self.assertTrue(result.ok)
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)
        self.assertEqual(
            result.metadata["raw_response_sha256"], hashlib.sha256(body).hexdigest()
        )
        request_body = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(request_body["gotoOptions"]["timeout"], 40_500)
        self.assertEqual(request_body["actionTimeout"], 40_500)
        self.assertEqual(result.metadata["service_timeout_ms"], 40_500)
        self.assertEqual(result.metadata["client_http_timeout_ms"], 40_500)

    @patch("harness.web_fetch.providers.cloudflare._post_multipart")
    @patch("harness.web_fetch.providers.cloudflare._get_document")
    def test_cloudflare_direct_pdf_uses_workers_ai_file_conversion(
        self, mock_get_document, mock_post_multipart
    ) -> None:
        pdf = b"%PDF-1.7\nexample"
        mock_get_document.return_value = HttpResponse(
            200,
            "https://arxiv.org/pdf/1706.03762",
            {"content-type": "application/pdf"},
            pdf,
        )
        api_body = json.dumps({
            "success": True,
            "result": [{
                "name": "1706.03762.pdf",
                "mimeType": "application/pdf",
                "format": "markdown",
                "data": "# Attention Is All You Need",
            }],
        }).encode()
        mock_post_multipart.return_value = HttpResponse(200, "u", {}, api_body)
        document = EvalCase(
            id="document-01",
            name="PDF",
            url="https://arxiv.org/pdf/1706.03762",
            category="document",
            expected_any=[],
            track="document",
        )

        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(document, 45)

        self.assertTrue(result.ok)
        self.assertEqual(result.content, "# Attention Is All You Need")
        self.assertEqual(result.metadata["route"], "workers_ai_tomarkdown")
        self.assertEqual(result.metadata["source_sha256"], hashlib.sha256(pdf).hexdigest())
        self.assertEqual(result.metadata["source_bytes"], len(pdf))
        self.assertEqual(result.metadata["source_content_type"], "application/pdf")
        self.assertLessEqual(
            mock_post_multipart.call_args.kwargs["timeout"], 40.5
        )
        self.assertEqual(mock_post_multipart.call_args.kwargs["data"], pdf)
        self.assertEqual(
            mock_post_multipart.call_args.args[0],
            "https://api.cloudflare.com/client/v4/accounts/a/ai/tomarkdown",
        )
        self.assertEqual(mock_get_document.call_args.args[0], document.url)

    @patch("harness.web_fetch.providers.cloudflare._post_multipart")
    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_document_viewer_stays_on_browser_route(
        self, mock_request, mock_post_multipart
    ) -> None:
        mock_request.return_value = HttpResponse(
            200, "u", {}, b'{"success":true,"result":"Viewer text"}'
        )
        viewer = EvalCase(
            id="document-07",
            name="Viewer",
            url="https://mozilla.github.io/pdf.js/web/viewer.html",
            category="document",
            expected_any=[],
            track="document",
        )

        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(viewer, 45)

        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["route"], "browser_rendering_markdown")
        self.assertIn("/browser-rendering/markdown", mock_request.call_args.args[0])
        mock_post_multipart.assert_not_called()

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_image_resource_uses_scrape_and_resolves_srcset(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            json.dumps({
                "success": True,
                "result": [{
                    "selector": "picture source",
                    "results": [{"attributes": [{
                        "name": "srcset",
                        "value": "small.jpg 480w, large.jpg 800w",
                    }]}],
                }],
            }).encode(),
        )
        image = EvalCase(
            id="image-x",
            name="Image",
            url="https://example.com/gallery/page.html",
            category="image",
            expected_any=[],
            track="image",
            task_kind="resource",
        )

        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(image, 45)

        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["route"], "browser_rendering_scrape_resources")
        self.assertEqual(result.metadata["images"], [
            "https://example.com/gallery/large.jpg",
            "https://example.com/gallery/small.jpg",
        ])
        self.assertIn("/browser-rendering/scrape", mock_request.call_args.args[0])
        selectors = mock_request.call_args.kwargs["json_body"]["elements"]
        self.assertEqual(selectors, [{"selector": "img"}, {"selector": "picture source"}])

    @patch("harness.web_fetch.providers.cloudflare.request")
    @patch("harness.web_fetch.providers.cloudflare._get_document")
    @patch("harness.web_fetch.providers.cloudflare._post_multipart")
    def test_kitesurf_cannot_receive_external_download_and_ai_conversion_credit(self, convert, download, native_request):
        case = EvalCase("pdf", "PDF", "https://example.com/paper.pdf", "document", [], track="document", task_kind="body")
        native_request.return_value = HttpResponse(503, "u", {}, b'{"success":false,"errors":[]}')
        result = CloudflareMarkdownProvider("kitesurf", {"api_token":"x", "account_id":"a", "browser":"kitesurf"}).fetch(case, 45)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "api_error")
        native_request.assert_called_once()
        self.assertIn("/browser-rendering/markdown?browser=kitesurf", native_request.call_args.args[0])
        download.assert_not_called()
        convert.assert_not_called()

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_kitesurf_resource_request_cannot_silently_select_chromium(self, mock_request):
        from urllib.parse import urlsplit, parse_qs
        mock_request.return_value = HttpResponse(200, "u", {}, b'{"success":true,"result":[]}')
        for track in ("image", "video"):
            case = EvalCase("resource", "Resource", "https://example.com/", track, [], track=track, task_kind="resource")
            CloudflareMarkdownProvider("kitesurf", {"api_token":"x", "account_id":"a", "browser":"kitesurf"}).fetch(case, 45)
            self.assertEqual(parse_qs(urlsplit(mock_request.call_args.args[0]).query).get("browser"), ["kitesurf"])

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_kitesurf_headless_route_keeps_page_resources_in_native_markdown(self, mock_request):
        mock_request.return_value = HttpResponse(
            200, "u", {}, b'{"success":true,"result":"![image](asset.png)"}'
        )
        case = EvalCase("resource", "Resource", "https://example.com/", "image", [],
                        track="image", task_kind="resource")
        result = CloudflareMarkdownProvider("kitesurf", {
            "api_token":"x", "account_id":"a", "browser":"kitesurf",
            "page_output":"native_markdown",
        }).fetch(case, 45)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "![image](asset.png)")
        self.assertIn("/browser-rendering/markdown?browser=kitesurf",
                      mock_request.call_args.args[0])
        self.assertEqual(result.metadata["route"], "browser_rendering_markdown")

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_video_resource_scrape_filters_ordinary_links(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            json.dumps({
                "success": True,
                "result": [{
                    "selector": "a[href]",
                    "results": [{"attributes": [
                        {"name": "href", "value": "clip.webm"},
                        {"name": "href", "value": "/about"},
                    ]}],
                }],
            }).encode(),
        )
        video = EvalCase(
            id="video-x",
            name="Video",
            url="https://example.com/watch/page.html",
            category="video",
            expected_any=[],
            track="video",
            task_kind="resource",
        )

        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(video, 45)

        self.assertEqual(result.metadata["videos"], [
            "https://example.com/watch/clip.webm"
        ])
        self.assertNotIn("/about", result.content)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_video_resource_keeps_packaged_video_downloads(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            json.dumps({
                "success": True,
                "result": [{
                    "selector": "a[href]",
                    "results": [
                        {"attributes": [{"name": "href", "value": "movie.mp4.zip"}]},
                        {"attributes": [{"name": "href", "value": "movie.webm.zip?mirror=1"}]},
                        {"attributes": [{"name": "href", "value": "notes.zip"}]},
                        {"attributes": [{"name": "href", "value": "../"}]},
                    ],
                }],
            }).encode(),
        )
        video = EvalCase(
            id="video-archive-x",
            name="Video download package",
            url="https://example.com/movies/",
            category="video",
            expected_any=[],
            track="video",
            task_kind="resource",
        )

        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(video, 45)

        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["videos"], [
            "https://example.com/movies/movie.mp4.zip",
            "https://example.com/movies/movie.webm.zip?mirror=1",
        ])
        self.assertNotIn("notes.zip", result.content)
        self.assertNotIn("\nhttps://example.com/\n", "\n" + result.content + "\n")

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_video_link_task_keeps_page_href_without_media_suffix(
        self, mock_request
    ) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            json.dumps({
                "success": True,
                "result": [{
                    "selector": "a[href]",
                    "results": [
                        {"attributes": [{
                            "name": "href",
                            "value": "//example.com/video/BV1234567890",
                        }]},
                        {"attributes": [{"name": "href", "value": "/about"}]},
                    ],
                }, {
                    "selector": "iframe",
                    "results": [{"attributes": [{
                        "name": "src", "value": "/short/iframe.html",
                    }]}],
                }],
            }).encode(),
        )
        video_link = EvalCase(
            id="video-link-x",
            name="Video detail entry",
            url="https://example.com/",
            category="video",
            expected_any=[],
            track="video",
            task_kind="resource",
            expected_resources=[{"id": "entry", "kind": "video-link"}],
        )

        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(video_link, 45)

        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["videos"], [
            "https://example.com/video/BV1234567890",
        ])
        self.assertIn("https://example.com/video/BV1234567890", result.content)
        self.assertNotIn("https://example.com/about", result.content)
        self.assertNotIn("https://example.com/short/iframe.html", result.content)
        self.assertEqual(mock_request.call_args.kwargs["json_body"]["elements"],
                         [{"selector": "a[href]"}])

    @patch("harness.web_fetch.providers.cloudflare.time.monotonic")
    @patch("harness.web_fetch.providers.cloudflare.urllib.request.build_opener")
    def test_cloudflare_multipart_read_enforces_one_total_deadline(
        self, mock_build_opener, mock_monotonic
    ) -> None:
        class Response:
            status = 200
            headers = {}
            fp = None

            def __init__(self) -> None:
                self.closed = False

            def read(self, _size: int) -> bytes:
                return b"partial"

            def geturl(self) -> str:
                return "https://api.cloudflare.com/client/v4/test"

            def close(self) -> None:
                self.closed = True

        response = Response()
        mock_build_opener.return_value.open.return_value = response
        mock_monotonic.side_effect = [100.0, 100.5, 101.1]

        with self.assertRaisesRegex(TimeoutError, "inner deadline"):
            _post_multipart(
                "https://api.cloudflare.com/client/v4/test",
                token="x",
                filename="file.pdf",
                content_type="application/pdf",
                data=b"pdf",
                timeout=1,
                max_bytes=100,
            )
        self.assertTrue(response.closed)

    @patch("harness.web_fetch.providers.cloudflare._post_multipart")
    @patch("harness.web_fetch.providers.cloudflare._get_document")
    def test_cloudflare_failed_document_download_does_not_fallback_or_convert(
        self, mock_get_document, mock_post_multipart
    ) -> None:
        body = b"origin error"
        mock_get_document.return_value = HttpResponse(
            503, "https://example.com/file.pdf", {"content-type": "text/plain"}, body
        )
        document = EvalCase(
            id="document-x",
            name="PDF",
            url="https://example.com/file.pdf",
            category="document",
            expected_any=[],
            track="document",
        )

        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(document, 45)

        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "http_status")
        self.assertEqual(result.metadata["route"], "workers_ai_tomarkdown")
        self.assertFalse(result.metadata["conversion_attempted"])
        self.assertEqual(base64.b64decode(result.metadata["_raw_response_b64"]), body)
        mock_post_multipart.assert_not_called()

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_identity_uses_observed_destination_only(self, mock_request):
        provider = CloudflareMarkdownProvider("cf", {"api_token": "x", "account_id": "a"})
        for meta, expected in (({}, None), ({"finalUrl": "https://example.com/final"},
                                          "https://example.com/final")):
            with self.subTest(meta=meta):
                body = json.dumps({"success": True, "result": "Body", "meta": meta}).encode()
                mock_request.return_value = HttpResponse(200, "u", {}, body)
                result = provider.fetch(self.case, 45)
                self.assertEqual(result.final_url, expected)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_empty_target_2xx_succeeds_but_origin_error_fails(self, mock_request) -> None:
        examples = (
            (
                b'{"success":true,"result":"","meta":{"status":200}}',
                None,
                None,
                200,
                True,
            ),
            (
                b'{"success":true,"result":"# Not the target","meta":{"status":404}}',
                "http_status",
                "Origin returned HTTP 404",
                404,
                False,
            ),
            (
                b'{"success":true,"result":"# Not the target","meta":{"status":"503"}}',
                "http_status",
                "Origin returned HTTP 503",
                503,
                False,
            ),
        )
        provider = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        )
        for body, error_type, message, upstream_status, empty in examples:
            with self.subTest(body=body):
                mock_request.return_value = HttpResponse(200, "u", {}, body)

                result = provider.fetch(self.case, 45)

                self.assertEqual(result.ok, upstream_status == 200)
                expected = json.loads(body)["result"]
                self.assertEqual(result.content, expected)
                self.assertEqual(result.error_type, error_type)
                self.assertEqual(result.error_message, message)
                self.assertEqual(result.metadata["upstream_status"], upstream_status)
                self.assertEqual(result.metadata["empty_content"], empty)
                self.assertEqual(
                    base64.b64decode(result.metadata["_raw_response_b64"]), body
                )

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_successful_empty_envelope_is_request_success_but_private(
        self, mock_request
    ) -> None:
        secret = "private-cloudflare-diagnostic"
        body = json.dumps({
            "success": True,
            "result": "",
            "errors": [{"message": secret}],
            "meta": {"status": 200},
        }).encode()
        mock_request.return_value = HttpResponse(200, "u", {}, body)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(
                output_dir=root,
                dataset_meta={"id": "cloudflare-envelope-test"},
                dataset_path=root / "missing-dataset.json",
                cases=[self.case],
                providers=[CloudflareMarkdownProvider(
                    "cloudflare", {"api_token": "x", "account_id": "a"}
                )],
                provider_config_path=root / "missing-providers.json",
                runs=1,
                timeout=2,
                delay=0,
            )
            record = records[0]
            artifact = (root / record["artifact_path"]).read_bytes()
            public_results = (root / "results.jsonl").read_bytes()
            private_response = root / record["provider_metadata"]["raw_response_path"]
            private_response_bytes = private_response.read_bytes()

        self.assertTrue(record["ok"])
        self.assertIsNone(record["error_type"])
        self.assertEqual(artifact, b"")
        self.assertNotIn(secret.encode(), public_results)
        self.assertEqual(private_response_bytes, body)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_content_preserves_origin_error_page_for_scoring(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            200,
            "u",
            {},
            b'{"success":true,"result":"<html>Not found</html>","meta":{"status":404}}',
        )

        result = CloudflareContentProvider(
            "cf-content", {"api_token": "x", "account_id": "a"}
        ).fetch(self.case, 2)

        self.assertFalse(result.ok)
        self.assertEqual(result.content, "<html>Not found</html>")
        self.assertEqual(result.error_type, "http_status")
        self.assertEqual(result.metadata["upstream_status"], 404)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_frozen_html_disables_dynamic_resources(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            200, "u", {}, b'{"success":true,"result":"# Example","meta":{"status":200}}'
        )
        provider = CloudflareMarkdownProvider("cf", {"api_token": "x", "account_id": "a"})
        result = provider.fetch_html(self.case, "<h1>Example</h1>", 1)
        self.assertTrue(result.ok)
        payload = mock_request.call_args.kwargs["json_body"]
        self.assertEqual(payload["html"], "<h1>Example</h1>")
        self.assertNotIn("setJavaScriptEnabled", payload)
        self.assertIn("document", payload["rejectResourceTypes"])
        self.assertIn("script", payload["rejectResourceTypes"])
        self.assertNotIn("url", payload)
        self.assertEqual(result.metadata["input_mode"], "inline_html")

    def test_provider_exposes_plan_rate_interval(self) -> None:
        provider = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a", "min_interval_seconds": 10.5}
        )
        self.assertEqual(provider.min_interval_seconds, 10.5)

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_rate_limit_is_classified(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            429,
            "u",
            {"retry-after": "10"},
            b'{"success":false,"errors":[{"message":"Too many requests"}]}',
        )
        result = CloudflareMarkdownProvider(
            "cf", {"api_token": "x", "account_id": "a"}
        ).fetch(self.case, 1)
        self.assertEqual(result.error_type, "rate_limit")
        self.assertEqual(result.metadata["retry_after"], "10")

    @patch("harness.web_fetch.providers.cloudflare.request")
    def test_cloudflare_content_reads_rendered_html(self, mock_request) -> None:
        mock_request.return_value = HttpResponse(
            200, "u", {}, b'{"success":true,"result":"<html>Example</html>"}'
        )
        result = CloudflareContentProvider(
            "cf-content", {"api_token": "x", "account_id": "a"}
        ).fetch(self.case, 1)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["output_contract"], "rendered_html")

    def test_cowagent_requires_real_checkout(self) -> None:
        provider = CowAgentBuiltinProvider("cowagent", {"repo_path": "/definitely/missing"})
        self.assertTrue(provider.validate())

    def test_cowagent_cannot_omit_declared_identity(self) -> None:
        errors = CowAgentBuiltinProvider("cowagent", {"repo_path": "."}).validate()
        self.assertIn("expected_repo_revision", errors)
        self.assertIn("required runtime paths", errors)

    def test_cowagent_removes_workspace_path_but_keeps_document_text(self) -> None:
        content = (
            "[Document: guide.pdf | Size: 10KB | Saved to: "
            "/Users/person/.browser-eval/runs/work/tmp/guide.pdf]\n\nBody"
        )
        self.assertEqual(
            _sanitize_public_content(content),
            "[Document: guide.pdf | Size: 10KB]\n\nBody",
        )


if __name__ == "__main__":
    unittest.main()
