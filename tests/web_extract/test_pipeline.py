import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_extract.example import create
from harness.web_extract.pipeline import (
    digest,
    evaluate_predictions,
    load_run,
    normalize,
    probe_cases,
    read_json,
)
from harness.web_extract.report import check, image_width

pytestmark = pytest.mark.usefixtures("stubbed_serial_collection")


class PipelineScenarios(unittest.TestCase):
    def test_browserbase_native_json_extracts_fields_without_external_model(self):
        from harness.web_extract.pipeline import SCHEMA, provider_call
        from harness.web_fetch.providers.http_client import HttpResponse

        payload = {
            "statusCode": 200,
            "content": {"price": 0, "sku": "0007", "availability": "OutOfStock"},
        }
        response = HttpResponse(
            200, "https://api.browserbase.com/v1/fetch", {}, json.dumps(payload).encode()
        )
        with (
            patch.dict(os.environ, {"BROWSERBASE_API_KEY": "synthetic"}),
            patch("harness.web_extract.pipeline.request", return_value=response) as call,
        ):
            result = provider_call(
                "browserbase-json",
                "<html>frozen</html>",
                "https://original.example/product",
                "https://snapshot.example/hash.html",
            )
        self.assertEqual(
            call.call_args.kwargs["json_body"],
            {
                "url": "https://snapshot.example/hash.html",
                "format": "json",
                "schema": SCHEMA,
                "proxies": False,
            },
        )
        self.assertEqual(normalize("browserbase-json", result["response"])["sku"], "0007")
        self.assertEqual(normalize("browserbase-json", payload)["price"], "0")
        for invalid in (
            {"statusCode": 403, "content": payload["content"]},
            {"content": payload["content"]},
            {"statusCode": 200, "content": "raw HTML"},
        ):
            self.assertIsNone(normalize("browserbase-json", invalid))
        self.assertEqual(
            provider_call("browserbase-json", "HTML", "https://original.example")["state"],
            "input_blocked",
        )

    def test_access_pause_and_resume_preserve_failed_physical_calls(self):
        from harness.web_extract.pipeline import probe, save_json

        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            truth = {str(i): {"url": "https://example.org/" + str(i)} for i in range(3)}
            save_json(home / "dataset/dataset/ground-truth.json", truth)
            save_json(
                home / "dataset/manifest.json",
                {
                    "synthetic": True,
                    "files": {
                        "html/" + key + ".html": {
                            "sha256": hashlib.sha256(b"<html>input</html>").hexdigest()
                        }
                        for key in truth
                    },
                },
            )
            (home / "dataset/html").mkdir()
            for key in truth:
                (home / "dataset/html" / (key + ".html")).write_text(
                    "<html>input</html>", encoding="utf-8"
                )
            run = home / "runs/resumable"
            denied = {
                "state": "response_received",
                "http_status": 402,
                "response": {"error": "Insufficient credits"},
            }
            accepted = {
                "state": "response_received",
                "http_status": 200,
                "response": {"result": {"sku": "present"}},
            }
            with (
                patch("harness.web_extract.pipeline.validate_dataset"),
                patch("harness.web_extract.pipeline.load_runtime_env"),
                patch.dict(
                    os.environ,
                    {"CLOUDFLARE_ACCOUNT_ID": "synthetic", "CLOUDFLARE_API_TOKEN": "synthetic"},
                ),
                patch(
                    "harness.web_extract.pipeline.provider_call",
                    side_effect=[denied, accepted, accepted, accepted],
                ) as call,
            ):
                with self.assertRaises(PermissionError):
                    probe(home, ["cloudflare-json"], full=True, run_dir=run)
                self.assertFalse((run / "manifest.json").exists())
                probe(home, ["cloudflare-json"], full=True, run_dir=run)
                self.assertEqual(call.call_count, 4)
                probe(home, ["cloudflare-json"], full=True, run_dir=run)
                self.assertEqual(call.call_count, 4)
            _, rows = load_run(run)
            self.assertEqual(len(rows), 3)
            manifest = read_json(run / "manifest.json")
            self.assertEqual(len(manifest["physical_calls"]), 4)
            first = run / manifest["physical_calls"][0]["path"]
            self.assertEqual(read_json(first)["http_status"], 402)
            first.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Physical call"):
                load_run(run)

    def test_browser_capture_png_and_jpeg_dimensions(self):
        import struct

        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1440, 900)
        jpeg = (
            b"\xff\xd8\xff\xe0\x00\x04AB\xff\xc0\x00\x11\x08"
            + struct.pack(">HH", 844, 390)
            + b"\x03"
            + b"\x01\x11\x00" * 3
            + b"\xff\xd9"
        )
        self.assertEqual(image_width(png), 1440)
        self.assertEqual(image_width(jpeg), 390)
        with self.assertRaises(ValueError):
            image_width(b"not an image")

    def test_missing_value_is_not_zero_or_instock(self):
        self.assertEqual(
            normalize(
                "cloudflare-json", {"result": {"price": None, "sku": None, "availability": None}}
            ),
            {},
        )
        self.assertEqual(
            normalize(
                "firecrawl-json",
                {"data": {"json": {"price": 0, "sku": "0007", "availability": "OutOfStock"}}},
            ),
            {"price": "0", "sku": "0007", "availability": "OutOfStock", "OutOfStock": "OutOfStock"},
        )
        self.assertIsNone(normalize("firecrawl-json", {"error": "quota exhausted"}))

    def test_native_product_boolean_and_price_amount(self):
        payload = {
            "objects": [
                {
                    "offerPrice": "$7.99",
                    "offerPriceDetails": {"amount": 7.99},
                    "sku": "007",
                    "availability": False,
                }
            ]
        }
        self.assertEqual(
            normalize("diffbot-product", payload),
            {
                "price": "7.99",
                "sku": "007",
                "availability": "OutOfStock",
                "OutOfStock": "OutOfStock",
            },
        )
        self.assertIsNone(normalize("diffbot-product", {"objects": [{}, {}]}))

    def test_native_offer_order_and_url_api_envelope(self):
        payload = {
            "data": {
                "identifiers": {"sku": "007"},
                "offers": [
                    {"price": {"amount": 9.5}, "availability": "unavailable"},
                    {"price": 1, "availability": "available"},
                ],
            }
        }
        self.assertEqual(
            normalize("scrapfly-product", payload),
            {
                "price": "9.5",
                "sku": "007",
                "availability": "OutOfStock",
                "OutOfStock": "OutOfStock",
            },
        )
        self.assertEqual(
            normalize(
                "oxylabs-json",
                {"results": [{"json": {"sku": "0008", "price": None, "availability": None}}]},
            ),
            {"sku": "0008"},
        )
        self.assertIsNone(normalize("oxylabs-json", {"results": [{"json": {}}, {"json": {}}]}))

    def test_cases_cover_disjoint_predeclared_strata(self):
        truth = {
            "c": {"price": []},
            "b": {"OutOfStock": ["OutOfStock"]},
            "a": {"InStock": ["InStock"], "price": ["3"]},
        }
        self.assertEqual(probe_cases(truth), ["a", "b", "c"])
        with self.assertRaises(ValueError):
            probe_cases({"a": {}})

    def test_no_population_filtering(self):
        with self.assertRaisesRegex(ValueError, "exact declared population"):
            evaluate_predictions({"a": {}, "b": {}}, {"a": {}}, {})

    def test_secret_projection(self):
        from harness.web_extract.pipeline import redact

        with patch.dict(
            os.environ,
            {
                "SGAI_API_KEY": "sgai-private-example",
                "CLOUDFLARE_ACCOUNT_ID": "account-private-example",
            },
        ):
            self.assertNotIn(
                "private-example",
                json.dumps(
                    redact(
                        {
                            "message": "sgai-private-example account-private-example",
                            "Authorization": "Bearer abc",
                            "SGAI-APIKEY": "sgai-private-example",
                        }
                    )
                ),
            )
        self.assertEqual(
            redact(
                {
                    "promptTokens": 100,
                    "completionTokens": 20,
                    "api_key": "secret",
                    "access_token": "credential",
                }
            ),
            {
                "promptTokens": 100,
                "completionTokens": 20,
                "api_key": "[REDACTED]",
                "access_token": "[REDACTED]",
            },
        )

    def test_symlinked_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "real").mkdir()
            (root / "real/a.json").write_text("{}", encoding="utf-8")
            (root / "link").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(ValueError):
                read_json(root / "link/a.json")

    def test_full_execution_keeps_failed_and_unselected_probe_cases(self):
        from harness.web_extract.pipeline import probe, save_json

        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            truth = {str(i): {"url": "https://example.org/" + str(i)} for i in range(4)}
            save_json(home / "dataset/dataset/ground-truth.json", truth)
            save_json(
                home / "dataset/manifest.json",
                {
                    "synthetic": True,
                    "files": {
                        "html/" + key + ".html": {
                            "sha256": hashlib.sha256(b"<html>synthetic</html>").hexdigest()
                        }
                        for key in truth
                    },
                },
            )
            (home / "dataset/html").mkdir()
            for key in truth:
                (home / "dataset/html" / (key + ".html")).write_text(
                    "<html>synthetic</html>", encoding="utf-8"
                )

            def call(method, html, url, fixture):
                return {
                    "state": "response_received",
                    "http_status": 500 if url.endswith("/1") else 200,
                    "response": {"result": {"price": None, "sku": None, "availability": None}},
                    "elapsed_seconds": 1.0,
                }

            with (
                patch("harness.web_extract.pipeline.validate_dataset"),
                patch("harness.web_extract.pipeline.load_runtime_env"),
                patch("harness.web_extract.pipeline.provider_call", side_effect=call) as mocked,
                patch.dict(
                    os.environ,
                    {"CLOUDFLARE_ACCOUNT_ID": "example", "CLOUDFLARE_API_TOKEN": "example"},
                ),
            ):
                run = probe(home, ["cloudflare-json"], full=True)
            contract, rows = load_run(run)
            self.assertEqual(contract["kind"], "full_archived_extraction")
            self.assertEqual({r["case_id"] for r in rows}, set(truth))
            self.assertEqual(mocked.call_count, 4)
            self.assertEqual(sum(r["http_status"] == 500 for r in rows), 1)

    def test_declared_pacing_spaces_requests_without_retrying_failures(self):
        from harness.web_extract.pipeline import probe, save_json

        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            truth = {str(i): {"url": "https://example.org/" + str(i)} for i in range(3)}
            save_json(home / "dataset/dataset/ground-truth.json", truth)
            save_json(
                home / "dataset/manifest.json",
                {
                    "synthetic": True,
                    "files": {
                        "html/" + key + ".html": {
                            "sha256": hashlib.sha256(b"<html>input</html>").hexdigest()
                        }
                        for key in truth
                    },
                },
            )
            (home / "dataset/html").mkdir()
            for key in truth:
                (home / "dataset/html" / (key + ".html")).write_text(
                    "<html>input</html>", encoding="utf-8"
                )
            clock = [0.0]
            starts = []

            def sleep(seconds):
                clock[0] += seconds

            def call(method, html, url, fixture):
                starts.append(clock[0])
                clock[0] += 5
                return {
                    "state": "response_received",
                    "http_status": 429,
                    "response": {"message": "Rate limit"},
                    "elapsed_seconds": 5,
                }

            with (
                patch("harness.web_extract.pipeline.validate_dataset"),
                patch("harness.web_extract.pipeline.load_runtime_env"),
                patch("harness.web_extract.pipeline.provider_call", side_effect=call),
                patch("harness.web_extract.pipeline.time.monotonic", side_effect=lambda: clock[0]),
                patch("harness.web_extract.pipeline.time.sleep", side_effect=sleep),
                patch("harness.benchmark_execution.threading.Event.wait", side_effect=sleep),
                patch.dict(os.environ, {"DIFFBOT_API_TOKEN": "synthetic"}),
            ):
                run = probe(home, ["diffbot-product"], full=True, min_call_interval=60)
            contract, rows = load_run(run)
            self.assertEqual(starts, [0, 60, 120])
            self.assertEqual(contract["minimum_call_interval_seconds"], 60)
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(r["http_status"] == 429 for r in rows))


class ReportScenarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "report"
        self.home = create(self.output, language="en")

    def test_success_failure_evidence_and_language_parity(self):
        result = check(self.output)
        self.assertEqual(result["observations"], 6)
        self.assertEqual(result["pages_per_language"], 15)
        for lang in ("en", "zh"):
            soup = BeautifulSoup(
                (self.output / lang / "results.html").read_text(encoding="utf-8"), "html.parser"
            )
            self.assertEqual(len(soup.select("tr[data-status]")), 6)
            control = soup.select_one('[data-result-filter="status"] option')
            self.assertEqual(control["value"], "fail")
            detail = next((self.output / lang).glob("case-*.html")).read_text(
                encoding="utf-8"
            )
            self.assertIn("Synthetic route rejects archived HTML", detail)
            check_presentation(detail)

    def test_default_navigation_groups_resources_on_every_product_route(self):
        for lang in ("en", "zh"):
            for path in (self.output / lang).glob("*.html"):
                soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                primary = soup.select("nav > a")
                self.assertEqual(len(primary), 5, path.name)
                self.assertEqual(
                    [a["href"] for a in primary],
                    [
                        f"{lang}/{route}.html"
                        for route in ("index", "results", "dataset", "protocol", "interpretation")
                    ],
                )
                resources = soup.select_one("nav details.nav-more")
                self.assertIsNotNone(resources, path.name)
                self.assertEqual(len(resources.select("a")), 4)
                self.assertEqual(len(soup.select(".report-tools #theme")), 1)
                self.assertEqual(len(soup.select(".report-tools a")), 1)

    def test_release_rejects_flat_navigation_even_with_valid_file_hashes(self):
        path = self.output / "en/index.html"
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        for link in list(soup.select("nav details a")):
            soup.nav.append(link.extract())
        path.write_text(str(soup), encoding="utf-8")
        manifest_path = self.output / "report-manifest.json"
        manifest = read_json(manifest_path)
        manifest["files"]["en/index.html"] = digest(path.read_bytes())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Report navigation"):
            check(self.output)

    def test_chinese_only_build_keeps_an_actionable_english_notice(self):
        output = self.output.parent / "chinese-only"
        create(output, language="zh")
        manifest = read_json(output / "report-manifest.json")
        self.assertEqual(manifest["languages"], ["zh"])
        self.assertEqual({p.name for p in (output / "en").glob("*.html")}, {"index.html"})
        notice = BeautifulSoup(
            (output / "en/index.html").read_text(encoding="utf-8"), "html.parser"
        )
        self.assertTrue(notice.select_one("[data-language-notice] a[href='zh/index.html']"))
        page = BeautifulSoup(
            (output / "zh/accounts.html").read_text(encoding="utf-8"), "html.parser"
        )
        self.assertEqual(page.find("a", string="English")["href"], "en/index.html")
        check(output)
        (output / "en/stale.html").write_text("old English", encoding="utf-8")
        with self.assertRaises(ValueError):
            check(output)

    def test_hosted_root_base_preserves_assets_navigation_and_evidence(self):
        from urllib.parse import urljoin, urlsplit

        root = "https://example.org/api/preview/signed-site/"
        for lang in ("en", "zh"):
            other = "zh" if lang == "en" else "en"
            for path in (self.output / lang).glob("*.html"):
                soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                self.assertEqual(soup.select_one('link[rel="stylesheet"]')["href"], "style.css")
                self.assertEqual(soup.select_one("script[src]")["src"], "app.js")
                self.assertIn(f"{lang}/index.html", [a["href"] for a in soup.select("nav a")])
                self.assertTrue(
                    any(a["href"].startswith(other + "/") for a in soup.select(".report-tools a"))
                )
                for el in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
                    value = el.get("href") or el.get("src") or el.get("data-src")
                    if urlsplit(value).scheme or urlsplit(value).netloc:
                        continue
                    hosted = urljoin(root, value)
                    local = urljoin((self.output / lang / path.name).as_uri(), "../" + value)
                    self.assertTrue(hosted.startswith(root), hosted)
                    self.assertTrue(Path(urlsplit(local).path).is_file(), local)
            detail = BeautifulSoup(
                next((self.output / lang).glob("case-*.html")).read_text(encoding="utf-8"),
                "html.parser",
            )
            self.assertTrue(
                detail.select_one("[data-src]")["data-src"].startswith("public-evidence/")
            )
        self.assertIsNotNone(
            BeautifulSoup(
                (self.output / "index.html").read_text(encoding="utf-8"), "html.parser"
            ).head
        )

    def test_release_rejects_page_relative_hosted_references(self):
        path = self.output / "en/index.html"
        path.write_text(
            path.read_text(encoding="utf-8").replace('href="style.css"', 'href="../style.css"'),
            encoding="utf-8",
        )
        manifest_path = self.output / "report-manifest.json"
        manifest = read_json(manifest_path)
        manifest["files"]["en/index.html"] = digest(path.read_bytes())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hosted site root"):
            check(self.output)

    def test_tampered_output_rejected(self):
        p = self.output / "en/index.html"
        p.write_text(p.read_text(encoding="utf-8").replace("0.800", "0.900"), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.output)

    def test_release_rejects_missing_visual_evidence(self):
        with self.assertRaisesRegex(ValueError, "actual visual coverage"):
            check(self.output, release=True)

    def test_release_rejects_desktop_capture_labeled_narrow(self):
        receipt = self.home / "visual.json"
        receipt.write_text(
            json.dumps(
                {
                    "report_manifest_sha256": digest(
                        (self.output / "report-manifest.json").read_bytes()
                    ),
                    "observations": [{"viewport": "narrow", "viewport_width": 1440}],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "viewport dimensions"):
            check(self.output, release=True, visual_receipt=receipt)

    def test_negative_highlight_summary_error_and_dialog_controls(self):
        good = (self.output / "en/index.html").read_text(encoding="utf-8")
        for bad in [
            good.replace("comparison-winner", "removed"),
            good.replace('data-sort="0.8"', 'data-sort="0.1"'),
        ]:
            with self.assertRaises(ValueError):
                check_presentation(bad)
        detail = next((self.output / "en").glob("case-*.html")).read_text(
            encoding="utf-8"
        )
        for bad in [
            detail.replace("data-result-summary", "missing-summary"),
            detail.replace("data-failure-diagnosis", "missing-diagnosis"),
            detail.replace('data-kind="error"', 'data-kind="other"'),
            detail.replace('id="evidence-synthetic-blocked"', 'id="wrong"'),
        ]:
            with self.assertRaises(ValueError):
                check_presentation(bad)

    def test_changed_or_incomplete_calls_rejected(self):
        run = self.home / "runs/example-v1"
        manifest = read_json(run / "manifest.json")
        manifest["calls"].pop()
        (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_run(run)


if __name__ == "__main__":
    unittest.main()
