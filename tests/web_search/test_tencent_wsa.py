"""Native Yuanbao Search contracts, independent of provider availability."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_search.core import normalize, normalize_response, request_body, request_cost


def envelope(pages):
    return {"Response": {"Pages": pages, "RequestId": "fixture", "Version": "standard"}}


class YuanbaoSearchTests(unittest.TestCase):
    def test_profile_requires_an_explicit_matching_edition_before_collection(self):
        from harness.web_search.core import validate_profile
        from harness.web_search.example import synthetic_profile

        profile = synthetic_profile()
        profile['methods'] = [dict(profile['methods'][0], adapter='tencent-wsa',
                                   output_options={'edition': 'lite'})]
        for expected in (None, '', ' ', 1, 'standard'):
            with self.subTest(expected=expected):
                profile['methods'][0]['expected_response_version'] = expected
                with self.assertRaisesRegex(ValueError, 'expected response version'):
                    validate_profile(profile)
        profile['methods'][0].pop('expected_response_version')
        with self.assertRaisesRegex(ValueError, 'expected response version'):
            validate_profile(profile)
        profile['methods'][0]['expected_response_version'] = 'lite'
        validate_profile(profile)
        for options in (None, {}):
            profile['methods'][0]['output_options'] = options
            with self.assertRaisesRegex(ValueError, 'expected response version'):
                validate_profile(profile)
            profile['methods'][0]['expected_response_version'] = 'standard'
            validate_profile(profile)
            profile['methods'][0]['expected_response_version'] = 'lite'
        profile['methods'][0].pop('output_options')
        with self.assertRaisesRegex(ValueError, 'expected response version'):
            validate_profile(profile)

    def test_transport_failures_retry_without_becoming_edition_errors(self):
        from harness.web_search.example import synthetic_profile
        from harness.web_search.runner import run

        failures = [
            (0, 28, b"", "operation timed out"),
            (502, 0, b"<html>bad gateway</html>", ""),
            (200, 18, b'{"Response":', "partial transfer"),
        ]
        for status, exit_code, raw, error in failures:
            with (
                self.subTest(status=status, exit_code=exit_code),
                tempfile.TemporaryDirectory() as d,
            ):
                root = Path(d)
                profile = synthetic_profile()
                profile["methods"] = [
                    {
                        "id": "tencent-wsa",
                        "label": "Yuanbao",
                        "adapter": "tencent-wsa",
                        "endpoint": "https://fixture.invalid",
                        "key_env": "TENCENT_WSA_API_KEY",
                        "currency": "CNY",
                        "billing": "unknown",
                        "expected_response_version": "standard",
                    }
                ]
                profile["protocol"]["retry"] = True
                path = root / "input.json"
                path.write_text(json.dumps(profile), encoding="utf-8")
                requests = []

                def http(
                    url,
                    body=None,
                    *args,
                    status=status,
                    exit_code=exit_code,
                    raw=raw,
                    error=error,
                    requests=requests,
                    **kwargs,
                ):
                    receipt = dict(
                        http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""
                    )
                    if body is None:
                        return receipt, b"reference " * 20
                    requests.append(body)
                    if len(requests) == 1:
                        receipt.update(
                            http_status=status, transport_exit=exit_code, transport_error=error
                        )
                        return receipt, raw
                    return receipt, json.dumps(envelope([])).encode()

                with (
                    patch("harness.web_search.runner.http_call", side_effect=http),
                    patch("harness.web_search.runner.load_runtime_env"),
                    patch.dict(os.environ, TENCENT_WSA_API_KEY="fixture"),
                ):
                    run(path, root / "run", certified=True)
                self.assertEqual(len(requests), 3)
                self.assertTrue(json.loads((root / "run/receipt.json").read_bytes())["certified"])
                self.assertEqual(requests[0], requests[1])

    def test_editions_have_distinct_native_requests_and_cannot_be_mixed(self):
        self.assertEqual(
            request_body("tencent-wsa", {"query": "q"}, {"top_k": 5}, {"edition": "lite"}),
            {"Query": "q"},
        )
        for edition in ("standard", "flagship"):
            self.assertEqual(
                request_body("tencent-wsa", {"query": "q"}, {"top_k": 5}, {"edition": edition}),
                {"Query": "q", "Cnt": 10, "Mode": 0},
            )
        method = {"adapter": "tencent-wsa", "expected_response_version": "standard"}
        payload = envelope([])
        self.assertEqual(normalize_response(payload, method, {"top_k": 5}), [])
        payload["Response"]["Version"] = "lite"
        with self.assertRaisesRegex(ValueError, "ResponseVersionMismatch"):
            normalize_response(payload, method, {"top_k": 5})
        self.assertIsNone(
            request_cost(dict(method, billing="http-200", price_per_1000=20), 200, 0, payload)
        )

    def test_native_service_error_is_not_charged_as_a_successful_http_200(self):
        method = {"adapter": "tencent-wsa", "billing": "http-200", "price_per_1000": 20}
        self.assertEqual(request_cost(method, 200, 0, envelope([])), 0.02)
        self.assertEqual(
            request_cost(method, 200, 0, {"Response": {"Error": {"Code": "InternalError"}}}), 0
        )

    def test_explicit_home_import_copies_only_yuanbao_without_overwriting_repo_key(self):
        from harness.web_fetch.accounts import import_credentials

        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "home.env", Path(directory) / "repo.env"
            source.write_text(
                "export TENCENT_WSA_API_KEY='fixture' # account key\n"
                "UNRELATED_SECRET=leave-in-home\n",
                encoding="utf-8",
            )
            imported, missing = import_credentials(source, target, keys=("TENCENT_WSA_API_KEY",))
            self.assertEqual((imported, missing), (["TENCENT_WSA_API_KEY"], []))
            self.assertNotIn("UNRELATED_SECRET", target.read_text(encoding="utf-8"))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            target.write_text("TENCENT_WSA_API_KEY=existing\n", encoding="utf-8")
            self.assertEqual(
                import_credentials(source, target, keys=("TENCENT_WSA_API_KEY",)), ([], [])
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "TENCENT_WSA_API_KEY=existing\n")

    def test_http_200_account_error_stops_before_other_tasks_or_retries(self):
        from harness.web_search.example import synthetic_profile
        from harness.web_search.runner import run

        for code in (
            "UnauthorizedOperation",
            "ResourceNotFound",
            "ResourceUnavailable",
            "AuthFailure.InvalidAuthorization",
            "ResponseVersionMismatch: expected standard, received lite",
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                profile = synthetic_profile()
                profile["methods"] = [
                    {
                        "id": "tencent-wsa",
                        "label": "Yuanbao",
                        "adapter": "tencent-wsa",
                        "endpoint": "https://fixture.invalid",
                        "key_env": "TENCENT_WSA_API_KEY",
                        "currency": "CNY",
                        "billing": "unknown",
                        "expected_response_version": "standard",
                    }
                ]
                profile["protocol"]["retry"] = True
                path = root / "input.json"
                path.write_text(json.dumps(profile), encoding="utf-8")
                requests = []

                def http(url, body=None, *args, requests=requests, code=code, **kwargs):
                    receipt = dict(
                        http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""
                    )
                    if body is None:
                        return receipt, b"reference " * 20
                    requests.append(body)
                    payload = (
                        {"Response": {"Version": "lite", "Pages": []}}
                        if code.startswith("ResponseVersionMismatch:")
                        else {"Response": {"Error": {"Code": code}}}
                    )
                    return receipt, json.dumps(payload).encode()

                with (
                    patch("harness.web_search.runner.http_call", side_effect=http),
                    patch("harness.web_search.runner.load_runtime_env"),
                    patch.dict(os.environ, TENCENT_WSA_API_KEY="fixture"),
                ):
                    with self.assertRaisesRegex(ValueError, "Provider account error"):
                        run(path, root / "run", certified=True)
                self.assertEqual(len(requests), 1)
                interruption = json.loads((root / "run/interruption.json").read_bytes())
                self.assertEqual(interruption["account_error"], code)
                self.assertFalse((root / "run/receipt.json").exists())

                def recovered(url, body=None, *args, requests=requests, **kwargs):
                    receipt = dict(
                        http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""
                    )
                    if body is None:
                        return receipt, b"reference " * 20
                    requests.append(body)
                    return receipt, json.dumps(envelope([])).encode()

                with (
                    patch("harness.web_search.runner.http_call", side_effect=recovered),
                    patch("harness.web_search.runner.load_runtime_env"),
                    patch.dict(os.environ, TENCENT_WSA_API_KEY="fixed-fixture"),
                ):
                    run(path, root / "run", certified=True, resume=True)
                self.assertEqual(len(requests), 3)
                self.assertTrue(json.loads((root / "run/receipt.json").read_bytes())["certified"])
                archived = (root / "run/resume-1/previous-results.jsonl").read_text(
                    encoding="utf-8"
                )
                self.assertIn(code, archived)

    def test_five_result_comparison_requests_smallest_native_page(self):
        body = request_body("tencent-wsa", {"query": "中文 search"}, {"top_k": 5})
        self.assertEqual(body, {"Query": "中文 search", "Cnt": 10, "Mode": 0})
        self.assertEqual(request_body("tencent-wsa", {"query": "q"}, {"top_k": 10})["Cnt"], 10)
        with self.assertRaises(ValueError):
            request_body("tencent-wsa", {"query": "q"}, {"top_k": 5}, {"summary": True})

    def test_native_site_filter_preserves_other_query_operators(self):
        case = {
            "query": "site:example.org research after:2026-09-01",
            "family": "site",
            "domains": ["example.org"],
        }
        self.assertEqual(
            request_body("tencent-wsa", case, {"top_k": 5}),
            {"Query": "research after:2026-09-01", "Cnt": 10, "Mode": 0, "Site": "example.org"},
        )

    def test_dynamic_summary_is_not_page_body(self):
        page = {
            "url": "https://example.org/doc",
            "title": "Title",
            "passage": "Standard snippet",
            "content": "Dynamic summary",
            "date": "2026-11-11",
            "score": 0.9,
        }
        row = normalize(envelope([json.dumps(page)]), "tencent-wsa")[0]
        self.assertEqual(
            (row["snippet"], row["summary"], row["content"]),
            ("Standard snippet", "Dynamic summary", ""),
        )
        self.assertEqual(row["date"], "2026-11-11")

    def test_lite_and_empty_success_do_not_invent_optional_fields(self):
        self.assertEqual(normalize(envelope([]), "tencent-wsa"), [])
        row = normalize(envelope([json.dumps({"url": "https://example.org"})]), "tencent-wsa")[0]
        self.assertEqual((row["snippet"], row["summary"], row["content"]), ("", "", ""))

    def test_application_errors_and_broken_native_pages_are_failures(self):
        invalid = [
            {"Response": {"Error": {"Code": "UnauthorizedOperation", "Message": "bad key"}}},
            {"Response": {}},
            envelope(None),
            envelope([{}]),
            envelope(["not JSON"]),
            envelope(["null"]),
            envelope([json.dumps({"title": "no URL"})]),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize(payload, "tencent-wsa")

    def test_top_five_uses_original_slots_and_retains_full_native_payload(self):
        pages = [json.dumps({"url": f"https://example.org/{i}"}) for i in range(10)]
        payload = envelope(pages)
        method = {"adapter": "tencent-wsa"}
        protocol = {"top_k": 5, "normalize_top_k": True, "result_url_resolution": "request-url/1"}
        pages[7] = "bad entry outside evaluated slots"
        rows = normalize_response(payload, method, protocol)
        self.assertEqual([r["url"] for r in rows], [f"https://example.org/{i}" for i in range(5)])
        self.assertEqual(len(payload["Response"]["Pages"]), 10)
        pages[2] = "bad entry inside evaluated slots"
        with self.assertRaises(ValueError):
            normalize_response(payload, method, protocol)

    def test_unknown_billing_is_never_zero_or_a_guessed_tier(self):
        method = {"adapter": "tencent-wsa", "billing": "unknown", "price_per_1000": None}
        self.assertIsNone(request_cost(method, 200, 0, envelope([])))

    def test_certified_collection_and_report_reconstruct_native_evidence(self):
        from harness.web_search.example import synthetic_profile
        from harness.web_search.report import build, check, load_run
        from harness.web_search.runner import run

        profile = synthetic_profile()
        profile["methods"] = [
            {
                "id": "tencent-wsa",
                "label": "Yuanbao Search",
                "adapter": "tencent-wsa",
                "expected_response_version": "standard",
                "endpoint": "https://api.wsa.cloud.tencent.com/SearchPro",
                "key_env": "TENCENT_WSA_API_KEY",
                "currency": "CNY",
                "price_per_1000": None,
                "billing": "unknown",
            }
        ]
        profile["protocol"].update(normalize_top_k=True, result_url_resolution="request-url/1")
        sent = []

        def http(url, body=None, header=None, timeout=20):
            receipt = dict(http_status=200, elapsed_ms=10, transport_exit=0, transport_error="")
            if body is None:
                return receipt, b"independently observed reference " * 10
            sent.append((body, header))
            payload = (
                envelope([json.dumps({"url": "https://reference.invalid/0", "passage": "source"})])
                if len(sent) == 1
                else {
                    "Response": {"Error": {"Code": "InternalError", "Message": "fixture failure"}}
                }
            )
            return receipt, json.dumps(payload).encode()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "profile.json"
            source.write_text(json.dumps(profile), encoding="utf-8")
            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, TENCENT_WSA_API_KEY="fixture-secret"),
            ):
                run(source, root / "run", certified=True)
            _, rows, receipt = load_run(root / "run")
            self.assertTrue(receipt["certified"])
            self.assertEqual(len(rows[0]["results"]), 1)
            self.assertIn("InternalError", rows[1]["error"])
            self.assertEqual(rows[1]["results"], [])
            self.assertTrue(all(h == "Authorization: Bearer fixture-secret" for _, h in sent))
            self.assertTrue(all(b["Cnt"] == 10 for b, _ in sent))
            build(root / "report", source=root / "run")
            check(root / "report")
            manifest = (root / "report/manifest.json").read_text(encoding="utf-8")
            self.assertNotIn("fixture-secret", manifest)
