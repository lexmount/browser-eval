import asyncio
import base64
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.web_fetch.retrieval_hints import parse_retrieval_hints


def normalized_plan(hints):
    return parse_retrieval_hints("dynamic", hints, identifier="test").worker_dict()


class NativeFileWorkerTests(unittest.TestCase):
    def load_worker(self, strategy, *, web_crawler=object, pdf_crawler=object, pdf_scraper=object):
        def config(**kwargs):
            return SimpleNamespace(**kwargs)

        modules = {
            "crawl4ai": SimpleNamespace(
                AsyncWebCrawler=web_crawler,
                BrowserConfig=config,
                CrawlerRunConfig=config,
                CacheMode=SimpleNamespace(BYPASS="bypass"),
            ),
            "crawl4ai.async_crawler_strategy": SimpleNamespace(AsyncHTTPCrawlerStrategy=strategy),
            "crawl4ai.async_configs": SimpleNamespace(HTTPCrawlerConfig=config),
            "crawl4ai.processors.pdf": SimpleNamespace(
                PDFCrawlerStrategy=pdf_crawler,
                PDFContentScrapingStrategy=pdf_scraper,
            ),
        }
        spec = importlib.util.spec_from_file_location(
            "file_worker_test",
            Path(__file__).resolve().parents[1] / "scripts/oss/crawl4ai_worker.py",
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        return module

    def test_native_strategy_returns_files_without_extra_fetch(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.png"
            raw = b"\x89PNG\r\n\x1a\n" + b"x" * 40
            path.write_bytes(raw)

            class Strategy:
                def __init__(self, **kwargs):
                    calls.append(kwargs)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def crawl(self, url, config):
                    calls.append((url, config.page_timeout))
                    return SimpleNamespace(
                        status_code=200, html="", redirected_url=url, downloaded_files=[str(path)]
                    )

            worker = self.load_worker(Strategy)
            result = asyncio.run(
                worker.fetch_file(
                    {"url": "https://example.com/file.png", "timeout_seconds": 45}, directory
                )
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1], ("https://example.com/file.png", 45000))
            self.assertTrue(result["ok"])
            self.assertEqual(base64.b64decode(result["files"][0]["base64"]), raw)
            self.assertEqual(
                result["metadata"]["implementation"], "crawl4ai.AsyncHTTPCrawlerStrategy"
            )

    def test_download_paths_cannot_escape(self):
        worker = self.load_worker(object)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "allowed").mkdir()
            other = root / "secret"
            other.write_bytes(b"private")
            with self.assertRaises(ValueError):
                worker.encode_downloads([other], root / "allowed")

    def test_file_only_worker_does_not_start_browser(self):
        class Strategy:
            def __init__(self, browser_config):
                self.directory = browser_config.downloads_path

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def crawl(self, url, config):
                path = Path(self.directory) / "file.png"
                path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
                return SimpleNamespace(
                    status_code=200, html="", redirected_url=url, downloaded_files=[str(path)]
                )

        worker = self.load_worker(Strategy)

        def forbidden_browser(**kwargs):
            raise AssertionError("File path must not launch Chrome")

        worker.AsyncWebCrawler = forbidden_browser
        request = {
            "url": "https://example.com/file.png",
            "timeout_seconds": 45,
            "task_kind": "file",
        }
        output = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")),
            patch("sys.stdout", output),
        ):
            asyncio.run(worker.main())
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_direct_pdf_selection_is_limited_to_document_body_tasks(self):
        worker = self.load_worker(object)
        self.assertTrue(
            worker.is_direct_pdf_document(
                {
                    "url": "https://arxiv.org/pdf/1706.03762?download=1",
                    "track": "document",
                    "task_kind": "body",
                }
            )
        )
        self.assertTrue(
            worker.is_direct_pdf_document(
                {
                    "url": "https://example.com/report.PDF?download=1",
                    "track": "document",
                    "task_kind": "body",
                }
            )
        )
        self.assertTrue(
            worker.is_direct_pdf_document(
                {"url": "https://example.com/report.pdf", "task_kind": "body"}
            )
        )
        for request in (
            {"url": "https://example.com/report.docx", "track": "document", "task_kind": "body"},
            {"url": "https://example.com/viewer.html", "track": "document", "task_kind": "body"},
            {"url": "https://example.com/report.pdf", "track": "web", "task_kind": "body"},
            {"url": "https://example.com/report.pdf", "track": "document", "task_kind": "file"},
        ):
            self.assertFalse(worker.is_direct_pdf_document(request))

    def test_direct_pdf_uses_native_pdf_strategies_and_returns_markdown(self):
        calls = []
        pdf_crawler_instance = object()
        pdf_scraper_instance = object()

        def pdf_crawler():
            calls.append("pdf-crawler")
            return pdf_crawler_instance

        def pdf_scraper():
            calls.append("pdf-scraper")
            return pdf_scraper_instance

        class WebCrawler:
            def __init__(self, **kwargs):
                calls.append(("init", kwargs))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def arun(self, url, config):
                calls.append(("arun", url, config))
                return SimpleNamespace(
                    success=True,
                    markdown=SimpleNamespace(raw_markdown="extracted PDF text"),
                    status_code=200,
                    url=url,
                    error_message="",
                    media={"images": []},
                    links={"urls": []},
                )

        worker = self.load_worker(
            object, web_crawler=WebCrawler, pdf_crawler=pdf_crawler, pdf_scraper=pdf_scraper
        )
        request = {
            "url": "https://example.com/report.pdf",
            "track": "document",
            "task_kind": "body",
            "timeout_seconds": 45,
        }
        result = asyncio.run(worker._run_pdf_document(request))

        self.assertEqual(calls[0:2], ["pdf-scraper", "pdf-crawler"])
        self.assertEqual(calls[2], ("init", {"crawler_strategy": pdf_crawler_instance}))
        self.assertEqual(calls[3][0:2], ("arun", request["url"]))
        config = calls[3][2]
        self.assertIs(config.scraping_strategy, pdf_scraper_instance)
        self.assertEqual(config.page_timeout, 45000)
        self.assertFalse(config.verbose)
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "extracted PDF text")
        self.assertIsNone(result["status_code"])
        self.assertFalse(result["metadata"]["origin_status_code_available"])
        self.assertEqual(result["metadata"]["final_url_source"], "requested_url")
        self.assertEqual(
            result["metadata"]["implementation"],
            "crawl4ai.processors.pdf.PDFCrawlerStrategy+"
            "crawl4ai.processors.pdf.PDFContentScrapingStrategy",
        )
        self.assertEqual(result["metadata"]["output_contract"], "native_pdf_extracted_markdown")
        self.assertTrue(result["metadata"]["strategy_selected_before_call"])

    def test_main_routes_legacy_body_request_to_pdf_strategy(self):
        worker = self.load_worker(object)
        seen = []

        async def fetch_pdf(request):
            seen.append(request)
            return {"ok": True, "content": "PDF text", "files": []}

        def forbidden_browser(**kwargs):
            raise AssertionError("Direct PDF body must not launch the HTML crawler")

        worker.fetch_pdf_document = fetch_pdf
        worker.AsyncWebCrawler = forbidden_browser
        request = {
            "url": "https://example.com/report.pdf",
            "timeout_seconds": 45,
            "task_kind": "body",
        }
        output = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")),
            patch("sys.stdout", output),
        ):
            asyncio.run(worker.main())
        self.assertEqual(seen, [request])
        self.assertEqual(json.loads(output.getvalue())["content"], "PDF text")

    def test_direct_pdf_child_result_uses_parent_owned_cleaned_temp_directory(self):
        worker = self.load_worker(object)
        state = SimpleNamespace(payload=None, temp_directory=None, joins=[])

        class ReceiveConnection:
            def poll(self):
                return state.payload is not None

            def recv(self):
                return state.payload

            def close(self):
                pass

        class SendConnection:
            def close(self):
                pass

        class Process:
            exitcode = 0

            def __init__(self, *, target, args, daemon):
                self.args = args
                self.alive = True

            def start(self):
                state.temp_directory = self.args[1]
                (Path(state.temp_directory) / "orphan.pdf").write_bytes(b"pdf")
                state.payload = worker._pdf_failure(self.args[0], "fixture", "fixture")
                state.payload["ok"] = True
                state.payload["error_type"] = None
                state.payload["error_message"] = None
                state.payload["content"] = "PDF text"
                self.alive = False

            def join(self, timeout):
                state.joins.append(timeout)

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.alive = False

            def kill(self):
                self.alive = False

        context = SimpleNamespace(
            Pipe=lambda duplex: (ReceiveConnection(), SendConnection()),
            Process=Process,
        )
        request = {
            "url": "https://example.com/report.pdf",
            "track": "document",
            "task_kind": "body",
            "timeout_seconds": 45,
        }
        with patch.object(worker.multiprocessing, "get_context", return_value=context):
            result = asyncio.run(worker.fetch_pdf_document(request))
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "PDF text")
        self.assertFalse(Path(state.temp_directory).exists())
        self.assertEqual(result["metadata"]["execution_boundary"], "per_call_child_process")
        self.assertEqual(result["metadata"]["temporary_storage_cleanup"], "completed")

    def test_direct_pdf_timeout_terminates_child_and_cleans_temp_directory(self):
        worker = self.load_worker(object)
        state = SimpleNamespace(temp_directory=None, terminated=False, killed=False, joins=[])

        class ReceiveConnection:
            def poll(self):
                return False

            def close(self):
                pass

        class SendConnection:
            def close(self):
                pass

        class Process:
            exitcode = -15

            def __init__(self, *, target, args, daemon):
                self.args = args
                self.alive = True

            def start(self):
                state.temp_directory = self.args[1]
                (Path(state.temp_directory) / "partial.pdf").write_bytes(b"partial")

            def join(self, timeout):
                state.joins.append(timeout)

            def is_alive(self):
                return self.alive

            def terminate(self):
                state.terminated = True
                self.alive = False

            def kill(self):
                state.killed = True
                self.alive = False

        context = SimpleNamespace(
            Pipe=lambda duplex: (ReceiveConnection(), SendConnection()),
            Process=Process,
        )
        request = {
            "url": "https://example.com/report.pdf",
            "track": "document",
            "task_kind": "body",
            "timeout_seconds": 0.01,
        }
        with patch.object(worker.multiprocessing, "get_context", return_value=context):
            result = asyncio.run(worker.fetch_pdf_document(request))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "timeout")
        self.assertTrue(state.terminated)
        self.assertFalse(state.killed)
        self.assertFalse(Path(state.temp_directory).exists())
        self.assertEqual(result["metadata"]["temporary_storage_cleanup"], "completed")
        self.assertEqual(result["metadata"]["implementation"], worker.PDF_IMPLEMENTATION)

    def test_dynamic_completion_hints_become_bounded_wait_config(self):
        worker = self.load_worker(object)
        config, metadata = worker.dynamic_completion_config(
            {
                "track": "dynamic",
                "retrieval_plan": normalized_plan(
                    {
                        "version": 1,
                        "wait_until": "domcontentloaded",
                        "completion": {
                            "selector": ".quotes .quote",
                            "minimum_count": 10,
                            "timeout_ms": 30000,
                            "delay_ms": 250,
                        },
                    }
                ),
            }
        )
        self.assertEqual(
            config,
            {
                "wait_until": "domcontentloaded",
                "wait_for": 'js:() => document.querySelectorAll(".quotes .quote").length >= 10',
                "wait_for_timeout": 30000,
                "delay_before_return_html": 0.25,
            },
        )
        self.assertEqual(
            metadata,
            {
                "strategy": "selector_count_js",
                "wait_until": "domcontentloaded",
                "selector": ".quotes .quote",
                "frame_selector": None,
                "minimum_count": 10,
                "timeout_ms": 30000,
                "delay_ms": 250,
                "completion_support": "exact",
            },
        )

        config, metadata = worker.dynamic_completion_config(
            {
                "track": "dynamic",
                "retrieval_plan": normalized_plan(
                    {
                        "version": 1,
                        "wait_until": "load",
                        "completion": {
                            "selector": "main",
                            "minimum_count": 1,
                            "timeout_ms": 1000,
                            "delay_ms": 0,
                        },
                    }
                ),
            }
        )
        self.assertEqual(config["wait_for"], "css:main")
        self.assertEqual(metadata["strategy"], "css_selector")

    def test_non_dynamic_browser_route_uses_domcontentloaded(self):
        worker = self.load_worker(object)
        config, metadata = worker.dynamic_completion_config(
            {"track": "web", "retrieval_plan": None}
        )
        self.assertEqual(config, {})
        self.assertEqual(
            metadata,
            {"strategy": "lifecycle", "wait_until": "domcontentloaded"},
        )

    def test_dynamic_v2_extraction_becomes_bounded_post_wait_dom_scope(self):
        worker = self.load_worker(object)
        hints = {
            "version": 2,
            "wait_until": "domcontentloaded",
            "completion": {
                "selector": "button", "minimum_count": 16,
                "timeout_ms": 30000, "delay_ms": 250,
            },
            "extraction": {
                "selector": "main main > div > div", "maximum_count": 16,
                "exclude_selectors": ["button"], "fallback": "body",
            },
        }
        config, metadata = worker.dynamic_extraction_config({
            "track": "dynamic", "retrieval_plan": normalized_plan(hints),
        })
        self.assertEqual(config["css_selector"], "[data-browser-eval-extraction-root]")
        self.assertIn(".slice(0, maximumCount)", config["js_code"])
        self.assertIn("clone.querySelectorAll(excluded)", config["js_code"])
        self.assertEqual(config["js_code"].count('document.createElement("br")'), 2)
        self.assertEqual(metadata, {
            "strategy": "post_wait_bounded_dom_clone",
            "selector": "main main > div > div",
            "frame_selector": None,
            "maximum_count": 16,
            "maximum_count_enforced": True,
            "exclude_selectors": ["button"],
            "fallback": "body",
            "scope_support": "exact",
        })

    def test_main_applies_dynamic_completion_route_before_html_call(self):
        seen = []

        class WebCrawler:
            def __init__(self, **kwargs):
                seen.append(("init", kwargs))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def arun(self, url, config):
                seen.append(("arun", url, config))
                return SimpleNamespace(
                    success=True,
                    markdown=SimpleNamespace(raw_markdown="ready"),
                    redirected_url="https://example.com/final",
                    redirected_status_code=200,
                    model_dump=lambda **kwargs: {"html": "<main>ready</main>",
                                                  "markdown": {"raw_markdown": "ready"},
                                                  "success": True},
                    downloaded_files=[],
                    status_code=200,
                    url=url,
                    error_message="",
                    media={"images": [], "videos": [], "audios": []},
                    links={"internal": [{"href": "https://example.com/record", "text": "Native record"}], "external": []},
                )

        worker = self.load_worker(object, web_crawler=WebCrawler)
        hints = {
            "version": 1,
            "wait_until": "domcontentloaded",
            "completion": {
                "selector": ".quotes .quote",
                "minimum_count": 10,
                "timeout_ms": 30000,
                "delay_ms": 250,
            },
        }
        request = {
            "url": "https://example.com/dynamic",
            "timeout_seconds": 45,
            "task_kind": "body",
            "track": "dynamic",
            "retrieval_plan": normalized_plan(hints),
        }
        output = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")),
            patch("sys.stdout", output),
        ):
            asyncio.run(worker.main())

        config = seen[1][2]
        self.assertEqual(config.wait_until, "domcontentloaded")
        self.assertEqual(
            config.wait_for, 'js:() => document.querySelectorAll(".quotes .quote").length >= 10'
        )
        self.assertEqual(config.wait_for_timeout, 30000)
        self.assertEqual(config.page_timeout, 30000)
        self.assertEqual(config.delay_before_return_html, 0.25)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["final_url"], "https://example.com/final")
        self.assertEqual(payload["status_code"], 200)
        from harness.web_fetch.response_input import answer_input
        _, native_links, _, _ = answer_input(payload["content"].encode(), payload["metadata"])
        self.assertEqual(native_links, [{"title": "Native record", "url": "https://example.com/record"}])
        self.assertEqual(payload["raw_tool_response"]["html"], "<main>ready</main>")
        self.assertEqual(payload["raw_tool_response"]["markdown"]["raw_markdown"], payload["content"])
        self.assertEqual(
            payload["metadata"]["completion"],
            {
                "strategy": "selector_count_js",
                "wait_until": "domcontentloaded",
                "selector": ".quotes .quote",
                "frame_selector": None,
                "minimum_count": 10,
                "timeout_ms": 30000,
                "delay_ms": 250,
                "completion_support": "exact",
                "effective_route_timeout_ms": 30000,
            },
        )
        self.assertIsNone(payload["metadata"]["extraction"])

    def test_dynamic_completion_requires_runner_normalized_plan(self):
        worker = self.load_worker(object)
        with self.assertRaisesRegex(ValueError, "normalized retrieval_plan"):
            worker.dynamic_completion_config({"track": "dynamic"})

    def test_file_route_cannot_bypass_normalized_plan_boundary(self):
        worker = self.load_worker(object)
        with self.assertRaisesRegex(ValueError, "normalized retrieval_plan"):
            worker.require_normalized_retrieval_plan(
                {"track": "dynamic", "task_kind": "file"}
            )

    def test_dynamic_frame_plan_waits_for_child_count(self):
        worker = self.load_worker(object)
        hints = {
            "version": 1,
            "wait_until": "domcontentloaded",
            "completion": {
                "frame_selector": "iframe#iframe",
                "selector": "h3.family-name",
                "minimum_count": 14,
                "timeout_ms": 45000,
                "delay_ms": 3000,
            },
        }
        config, metadata = worker.dynamic_completion_config(
            {"track": "dynamic", "retrieval_plan": normalized_plan(hints)}
        )
        self.assertEqual(metadata["strategy"], "frame_selector_count_js")
        self.assertIn(
            'frame.contentDocument.querySelectorAll("h3.family-name")', config["wait_for"]
        )
        self.assertEqual(config["delay_before_return_html"], 3)

    def test_dynamic_timeout_failure_retains_completion_route_metadata(self):
        class WebCrawler:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def arun(self, url, config):
                await asyncio.sleep(1)

        worker = self.load_worker(object, web_crawler=WebCrawler)
        hints = {
            "version": 1,
            "wait_until": "domcontentloaded",
            "completion": {
                "selector": "main",
                "minimum_count": 1,
                "timeout_ms": 1000,
                "delay_ms": 0,
            },
        }
        request = {
            "url": "https://example.com/dynamic",
            "timeout_seconds": 0.01,
            "task_kind": "body",
            "track": "dynamic",
            "retrieval_plan": normalized_plan(hints),
        }
        output = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")),
            patch("sys.stdout", output),
        ):
            asyncio.run(worker.main())
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_type"], "TimeoutError")
        self.assertEqual(payload["metadata"]["completion"]["strategy"], "css_selector")
        self.assertEqual(payload["metadata"]["completion"]["effective_route_timeout_ms"], 10)


def test_native_default_worker_has_no_generated_wait_or_extraction():
    import importlib.util
    spec = importlib.util.spec_from_file_location("native_crawl_worker", Path("scripts/oss/crawl4ai_worker.py"))
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    request = {"track": "dynamic", "retrieval_plan": None, "retrieval_mode": "native_default"}
    completion, _ = worker.dynamic_completion_config(request)
    extraction, _ = worker.dynamic_extraction_config(request)
    assert completion == extraction == {}
