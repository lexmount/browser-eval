import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.report_interfaces import SPECS
from harness.web_fetch.report_methods import PROFILES


@unittest.skipUnless(os.name == "posix", "executable fixture uses POSIX shebang")
class MoliTests(unittest.TestCase):
    def test_resource_route_enumerates_bounded_same_origin_frame_documents(self):
        from harness.web_fetch.providers.moli import _RESOURCE_PAGE_EXPRESSION

        self.assertIn("frame.contentDocument", _RESOURCE_PAGE_EXPRESSION)
        self.assertIn("roots.length < 16", _RESOURCE_PAGE_EXPRESSION)
        self.assertNotIn("pic_trulli", _RESOURCE_PAGE_EXPRESSION)
    def test_cancellation_between_popen_and_handle_registration_is_latched(self):
        import subprocess

        provider = self.provider("import time\ntime.sleep(20)")
        self.assertEqual(provider.validate(), [])
        original = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            provider.cancel()  # Real child exists but fetch has not received its handle.
            return process

        provider.begin_call()
        with patch("harness.web_fetch.providers.moli.subprocess.Popen", side_effect=launch):
            result = provider.fetch(self.case, 1)
        self.assertFalse(result.ok)
        self.assertIsNotNone(children[0].poll())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.binary = Path(self.directory.name) / "moli fixture"
        self.case = EvalCase("example", "Example", "https://example.com/?a=1&b=2", "static", [])

    def provider(self, script, **config):
        self.binary.write_text(
            "#!" + sys.executable + "\n"
            "import sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('moli 1.1.4')\n"
            "    raise SystemExit(0)\n"
            + script
        )
        self.binary.chmod(0o700)
        config.setdefault("expected_reported_version", "moli 1.1.4")
        config.setdefault(
            "expected_binary_sha256", hashlib.sha256(self.binary.read_bytes()).hexdigest()
        )
        return build_provider(
            "moli-webfetch", dict(type="moli_webfetch", binary=str(self.binary), **config)
        )

    def case_with(self, **changes):
        values = dict(self.case.__dict__)
        values.update(changes)
        return SimpleNamespace(**values)

    def test_cli_contract_and_diagnostics(self):
        provider = self.provider(
            'import sys\nassert sys.argv[1:]==["fetch","--dump","markdown","--wait-until","done","--timeout","2000","--http-timeout","2000","--block-private-networks","https://example.com/?a=1&b=2"]\nprint("# Example Domain")\nprint("diagnostic only",file=sys.stderr)\n'
        )
        self.assertEqual(provider.validate(), [])
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "# Example Domain\n")
        self.assertIn("diagnostic only", result.metadata["stderr"])
        self.assertIsNone(result.status_code)
        self.assertIsNone(result.final_url)
        self.assertEqual(len(result.metadata["binary_sha256"]), 64)
        self.assertEqual(result.metadata["route"], "ordinary_markdown")
        self.assertEqual(result.metadata["output"], "markdown")
        self.assertEqual(result.metadata["wait"], {"wait_until": "done"})

    def test_video_body_uses_generic_answer_free_metadata_extraction(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" in sys.argv and "--dump" not in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "document.title" in expression and "document.body?.innerText" in expression\n'
            'assert "itemprop=\\\"author\\\"" in expression\n'
            'assert "application/ld+json" in expression\n'
            'assert "expected-video-title" not in expression\n'
            'assert "expected-publisher" not in expression\n'
            'print("A video title\\n\\nA publisher")'
        )
        case = self.case_with(
            track="video",
            task_kind="body",
            required_facts=[
                {"id": "title", "alternatives": ["expected-video-title"]},
                {"id": "publisher", "alternatives": ["expected-publisher"]},
            ],
        )
        result = provider.fetch(case, 2)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.content, "A video title\n\nA publisher\n")
        self.assertEqual(result.metadata["route"], "media_metadata_runtime")
        self.assertEqual(
            result.metadata["output"], "visible_text_and_standard_media_metadata"
        )
        self.assertEqual(
            result.metadata["wait"]["extraction"],
            "visible_text_and_standard_media_metadata",
        )

    def test_non_video_body_keeps_ordinary_markdown_route(self):
        provider = self.provider(
            "import sys\n"
            'assert "--dump" in sys.argv and "markdown" in sys.argv\n'
            'assert "--eval" not in sys.argv\n'
            'print("ordinary body")'
        )
        result = provider.fetch(self.case_with(track="static", task_kind="body"), 2)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "ordinary_markdown")

    def test_challenge_route_uses_one_json_trace_and_converts_final_dom(self):
        envelope = {
            "final_url": "https://example.com/final",
            "status": 200,
            "title": "Catalog",
            "headers": [{"name": "content-type", "value": "text/html"}],
            "redirect_chain": [{
                "from_url": "https://example.com/start",
                "to_url": "https://example.com/final",
                "status": 302,
            }],
            "html": '<main><a href="/item/1">Real product</a></main>',
            "network": {
                "main_document": {"status": 200, "redirected": True, "redirect_count": 1},
                "subresources": [{"ok": False, "error_text": "blocked"}],
            },
        }
        provider = self.provider(
            "import json,sys\n"
            'assert "--dump" in sys.argv and sys.argv[sys.argv.index("--dump")+1]=="json"\n'
            'assert "--trace-network" in sys.argv and "--eval" not in sys.argv\n'
            f"print({json.dumps(envelope)!r})"
        )
        result = provider.fetch(self.case_with(track="challenge", task_kind="structure"), 10)
        self.assertTrue(result.ok, repr(result))
        self.assertIn("[Real product]", result.content)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.final_url, "https://example.com/final")
        self.assertEqual(result.title, "Catalog")
        self.assertEqual(result.metadata["route"], "challenge_json_trace")
        self.assertEqual(result.metadata["network_trace"]["failed_subresource_count"], 1)
        self.assertEqual(result.metadata["network_trace"]["response_header_names"],
                         ["content-type"])
        self.assertIn("_raw_stdout_b64", result.metadata)

    def test_challenge_http_error_keeps_body_but_request_fails(self):
        envelope = {
            "final_url": "https://example.com/gate",
            "status": 403,
            "title": "Access denied",
            "headers": [],
            "redirect_chain": [],
            "html": "<h1>Access denied</h1>",
            "network": {},
        }
        provider = self.provider(f"print({json.dumps(envelope)!r})")
        result = provider.fetch(self.case_with(track="challenge", task_kind="structure"), 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "api_error")
        self.assertEqual(result.status_code, 403)
        self.assertIn("Access denied", result.content)

    def test_dynamic_task_without_hints_fails_closed(self):
        provider = self.provider('raise AssertionError("should not run")')
        result = provider.fetch(self.case_with(track="dynamic"), 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")

    def test_dynamic_task_prefers_predeclared_selector_without_answer_leakage(self):
        provider = self.provider(
            "import sys\n"
            'assert sys.argv[1]=="fetch" and "--eval" in sys.argv\n'
            'assert "--dump" not in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "main article" in expression and "node.innerText.split" in expression\n'
            'expected=["--wait-selector","main article","--wait-until","domcontentloaded",'
            '"--delay-ms","999","--timeout","10000","--http-timeout",'
            '"--block-private-networks","https://example.com/?a=1&b=2"]\n'
            'assert all(value in sys.argv for value in expected),sys.argv\n'
            'assert "answer-must-not-become-a-wait" not in sys.argv\n'
            'print("ready")'
        )
        case = self.case_with(
            track="dynamic",
            expected_any=["answer-must-not-become-a-wait"],
            reference_answer="answer-must-not-become-a-wait",
            retrieval_hints={
                "version": 1,
                "wait_until": "domcontentloaded",
                "completion": {
                    "selector": "main article",
                    "minimum_count": 1,
                    "timeout_ms": 10000,
                    "delay_ms": 999,
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(
            result.metadata["wait"],
            {
                "strategy": "selector",
                "selector": "main article",
                "minimum_count": 1,
                "frame_selector": None,
                "delay_ms": 999,
                "timeout_ms": 10000,
                "wait_until": "domcontentloaded",
                "completion_support": "exact",
                "extraction": "selected_records_or_rendered_body_fallback",
            },
        )
        self.assertEqual(result.metadata["wait_until"], "domcontentloaded")

    def test_selector_minimum_count_generates_only_bounded_count_predicate(self):
        provider = self.provider(
            "import sys\n"
            'assert sys.argv[4]=="--wait-script"\n'
            "assert sys.argv[5]=='() => document.querySelectorAll(\".row\").length >= 3'\n"
            'print("ready")'
        )
        case = self.case_with(
            track="dynamic",
            retrieval_hints={
                "version": 1,
                "wait_until": "domcontentloaded",
                "completion": {
                    "selector": ".row",
                    "minimum_count": 3,
                    "timeout_ms": 10000,
                    "delay_ms": 0,
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["wait"]["minimum_count"], 3)

    def test_dynamic_complete_records_wait_for_nonempty_text_fragments(self):
        provider = self.provider(
            "import sys\n"
            'assert sys.argv[4]=="--wait-script"\n'
            'wait=sys.argv[5]\n'
            'assert "nodes.length >= 12" in wait\n'
            'assert "nodes.slice(0, 12).every" in wait\n'
            'assert "innerText.split" in wait\n'
            'assert "Chaz Kangeroo Hoodie" not in wait and "$52" not in wait\n'
            'print("complete products")'
        )
        case = self.case_with(
            track="dynamic",
            retrieval_hints={
                "version": 1,
                "wait_until": "domcontentloaded",
                "completion": {
                    "selector": "#product-grid > div",
                    "minimum_count": 12,
                    "minimum_text_fragments": 2,
                    "timeout_ms": 10000,
                    "delay_ms": 0,
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["wait"]["minimum_text_fragments"], 2)

    def test_iframe_waits_for_child_count_and_serializes_frames(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" in sys.argv and "--wait-script" in sys.argv\n'
            'assert "frame.contentDocument.querySelectorAll(\\"h3.family-name\\")" in sys.argv[5]\n'
            'assert "--delay-ms" in sys.argv and "3000" in sys.argv\n'
            'assert "--dump" not in sys.argv and "--with-frames" not in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "iframe#iframe" in expression and "node.innerText.split" in expression\n'
            'print("Carettochelyidae")'
        )
        case = self.case_with(
            track="dynamic",
            retrieval_hints={
                "version": 1,
                "wait_until": "domcontentloaded",
                "completion": {
                    "frame_selector": "iframe#iframe",
                    "selector": "h3.family-name",
                    "minimum_count": 14,
                    "timeout_ms": 45000,
                    "delay_ms": 3000,
                },
            },
        )
        result = provider.fetch(case, 45)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["wait"]["strategy"], "frame_selector_count_script")
        self.assertEqual(result.metadata["route"], "dynamic_selected_text")

    def test_dynamic_selected_text_is_generic_and_answer_free(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" in sys.argv and "--wait-script" in sys.argv\n'
            'assert "--dump" not in sys.argv and "--with-frames" not in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert expression.startswith("(() =>") and expression.endswith("})()")\n'
            'assert "iframe#results" in expression and "h3.family-name" in expression\n'
            'assert "node.innerText.split" in expression and "root.body" in expression\n'
            'assert "answer-must-not-leak" not in expression\n'
            'print("Family One\\n\\nFamily Two")'
        )
        case = self.case_with(
            track="dynamic",
            expected_any=["answer-must-not-leak"],
            retrieval_hints={
                "version": 1,
                "wait_until": "domcontentloaded",
                "completion": {
                    "frame_selector": "iframe#results",
                    "selector": "h3.family-name",
                    "minimum_count": 2,
                    "timeout_ms": 10000,
                    "delay_ms": 0,
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "dynamic_selected_text")
        self.assertEqual(
            result.metadata["output"], "selected_records_or_rendered_body_fallback"
        )
        self.assertEqual(
            result.metadata["wait"]["extraction"],
            "selected_records_or_rendered_body_fallback",
        )

    def test_dynamic_selected_records_are_not_duplicated_by_page_body(self):
        provider = self.provider(
            "import sys\n"
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "? records.join(\'\\\\n\\\\n\') : body" in expression\n'
            'assert "records.join(\'\\\\n\\\\n\') +" not in expression\n'
            'print("record one\\n\\nrecord two")'
        )
        case = self.case_with(
            track="dynamic",
            retrieval_hints={
                "version": 1,
                "wait_until": "domcontentloaded",
                "completion": {
                    "selector": ".record",
                    "minimum_count": 2,
                    "timeout_ms": 10000,
                    "delay_ms": 0,
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.content, "record one\n\nrecord two\n")

    def test_dynamic_extraction_promotes_button_to_nearest_multiline_card(self):
        provider = self.provider(
            "import sys\n"
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "level < 3" in expression and "lines(node).length < 2" in expression\n'
            'assert "node.parentElement" in expression\n'
            'assert ")].slice(0, 16)" in expression\n'
            'wait=sys.argv[sys.argv.index("--wait-script")+1]\n'
            'assert "querySelectorAll(\\\"button\\\").length >= 16" in wait\n'
            'print("Free shipping\\nProduct\\n$10.00\\nor 2 x$5.00\\nAdd to cart")'
        )
        case = self.case_with(
            track="dynamic",
            retrieval_hints={
                "version": 1,
                "wait_until": "domcontentloaded",
                "completion": {
                    "selector": "button",
                    "minimum_count": 16,
                    "timeout_ms": 10000,
                    "delay_ms": 0,
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))
        self.assertNotIn("extraction_ancestor_levels", result.metadata["wait"])

    def test_resource_page_uses_bounded_runtime_url_discovery(self):
        provider = self.provider(
            "import sys\n"
            'assert "--dump" not in sys.argv\n'
            'assert "--eval" in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "[src],[href],[data],[poster],[srcset]" in expression\n'
            'assert "astro-island[component-url]" in expression\n'
            'assert not ({"--image","--video","--media","--resource"} & set(sys.argv))\n'
            'print("{\\\"links\\\":[\\\"https://example.com/movie.mp4\\\"]}")'
        )
        result = provider.fetch(self.case_with(track="image", task_kind="resource"), 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "resource_page_runtime")
        self.assertEqual(result.metadata["output"], "structured_resource_urls")
        self.assertIn("movie.mp4", result.content)

    def test_file_task_preserves_binary_stdout(self):
        from dataclasses import replace

        provider = self.provider(
            "import sys\n"
            'assert "--dump" not in sys.argv\n'
            'assert "--wait-until" not in sys.argv\n'
            'assert sys.argv[1:6]==["fetch","--timeout","2000","--http-timeout","2000"]\n'
            'sys.stdout.buffer.write(b"\\x89PNG\\r\\n\\x1a\\n" + b"x"*40)'
        )
        result = provider.fetch(replace(self.case, task_kind="file"), 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.files[0]["data"], b"\x89PNG\r\n\x1a\n" + b"x" * 40)
        self.assertEqual(result.content, "")
        self.assertIsNone(result.metadata["dump"])
        self.assertIsNone(result.metadata["wait_until"])
        self.assertIsNone(result.metadata["wait"])
        self.assertEqual(result.metadata["route"], "raw_file")
        self.assertEqual(result.metadata["output"], "raw_bytes")

    def test_direct_pdf_body_uses_raw_fetch_and_extracts_inside_call(self):
        import io
        from dataclasses import replace

        from pypdf import PdfWriter

        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(output)
        encoded = repr(output.getvalue())
        provider = self.provider(
            "import sys\n"
            'assert "--dump" not in sys.argv\n'
            'assert "--wait-until" not in sys.argv\n'
            'assert "--http-timeout" in sys.argv\n'
            f"sys.stdout.buffer.write({encoded})"
        )
        document = replace(
            self.case,
            url="https://example.com/fixed.PDF?download=1#page=2",
            track="document",
        )
        result = provider.fetch(document, 2)
        self.assertFalse(result.ok)  # A blank but valid PDF has no answer text.
        self.assertEqual(result.error_type, "empty_output")
        self.assertEqual(result.metadata["document_extractor"], "pypdf")
        self.assertEqual(result.metadata["dump"], None)
        self.assertEqual(result.metadata["route"], "raw_document")
        self.assertEqual(result.metadata["output"], "raw_document_bytes+local_text")
        self.assertIn("_raw_stdout_b64", result.metadata)

    def test_serialized_direct_document_fails_and_preserves_raw_evidence(self):
        from dataclasses import replace

        raw = b"<html><body>PK&#3;not-raw-docx</body></html>"
        provider = self.provider(
            "import sys\n"
            f"sys.stdout.buffer.write({raw!r})"
        )
        document = replace(
            self.case,
            url="https://example.com/demo.docx",
            track="document",
        )
        result = provider.fetch(document, 2)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "invalid_output")
        self.assertEqual(result.content, "")
        self.assertEqual(
            result.metadata["raw_stdout_sha256"], hashlib.sha256(raw).hexdigest()
        )
        self.assertEqual(base64.b64decode(result.metadata["_raw_stdout_b64"]), raw)
        self.assertEqual(
            result.metadata["document_response"], "serialized_text_not_raw_document"
        )

    def test_document_extraction_must_finish_inside_the_call_budget(self):
        import time

        provider = self.provider('import sys\nsys.stdout.buffer.write(b"%PDF-fixture")')
        document = self.case_with(url="https://example.com/fixed.pdf", track="document")

        def slow_extract(_raw):
            time.sleep(3.2)
            return "late content", "fixture"

        with patch(
            "harness.web_fetch.providers.moli.extract_document_text",
            side_effect=slow_extract,
        ):
            result = provider.fetch(document, 3)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "timeout")
        self.assertIn("extraction", result.error_message)

    def test_pdfjs_viewer_uses_runtime_document_text(self):
        provider = self.provider(
            "import json,sys\n"
            'assert "--dump" not in sys.argv and "--eval" in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "PDFViewerApplication" in expression and "getTextContent" in expression\n'
            'print(json.dumps({"kind":"text","source":"https://example.com/paper.pdf",'
            '"text":"TraceMonkey nested trace trees"}))'
        )
        result = provider.fetch(
            self.case_with(url="https://mozilla.github.io/pdf.js/web/viewer.html", track="document"),
            2,
        )
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.content, "TraceMonkey nested trace trees")
        self.assertEqual(result.metadata["route"], "document_page_runtime")
        self.assertEqual(result.metadata["document_extractor"], "pdfjs_runtime")

    def test_embedded_pdf_page_returns_bytes_for_local_extraction(self):
        encoded = base64.b64encode(b"%PDF-fixture").decode("ascii")
        provider = self.provider(
            "import json,sys\n"
            'assert "--dump" not in sys.argv and "--eval" in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "embed[src],iframe[src],object[data],a[href]" in expression\n'
            f'print(json.dumps({{"kind":"pdf_base64","source":"https://example.com/sample.pdf",'
            f'"data":"{encoded}"}}))'
        )
        with patch(
            "harness.web_fetch.providers.moli.extract_document_text",
            return_value=("embedded PDF text", "fixture"),
        ):
            result = provider.fetch(
                self.case_with(url="https://pdfobject.com/static/", track="document"), 2
            )
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.content, "embedded PDF text")
        self.assertEqual(result.metadata["document_extractor"], "fixture")
        self.assertEqual(result.metadata["embedded_document_url"], "https://example.com/sample.pdf")

    def test_invalid_dynamic_hints_fail_before_process(self):
        for hints in (
            "selector",
            {"wait_selector": "main"},
            {"wait_until": "forever"},
            {"completion": []},
            {"completion": {"selector": ""}},
            {"completion": {"minimum_count": 2}},
            {"completion": {"selector": "main", "minimum_count": 0}},
            {"completion": {"delay_ms": -1}},
            {"completion": {"wait_script": "() => answer"}},
        ):
            with self.subTest(hints=hints):
                provider = self.provider('raise AssertionError("should not run")')
                with patch("harness.web_fetch.providers.moli.subprocess.Popen") as process:
                    result = provider.fetch(
                        self.case_with(track="dynamic", retrieval_hints=hints), 2
                    )
                self.assertFalse(result.ok)
                self.assertEqual(result.error_type, "configuration")
                process.assert_not_called()

    def test_dynamic_raw_route_cannot_bypass_missing_hints(self):
        provider = self.provider('raise AssertionError("should not run")')
        case = self.case_with(track="dynamic", task_kind="file", retrieval_hints={})
        with patch("harness.web_fetch.providers.moli.subprocess.Popen") as process:
            result = provider.fetch(case, 2)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")
        process.assert_not_called()

    def test_failure_empty_binary_and_oversized_output(self):
        for script, config, expected in [
            ('import sys\nprint("partial")\nsys.exit(7)', {}, "tool_error"),
            ('print(" ")', {}, "empty_output"),
            ('import sys\nsys.stdout.buffer.write(b"\\xff")', {}, "invalid_output"),
            ('print("abcdef")', {"max_bytes": 3}, "output_too_large"),
            ('import sys\nprint("abcdef",file=sys.stderr)', {"max_bytes": 3}, "output_too_large"),
        ]:
            with self.subTest(expected=expected):
                result = self.provider(script, **config).fetch(self.case, 2)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_type, expected)

    def test_timeout_is_failure(self):
        result = self.provider("import time\ntime.sleep(10)").fetch(self.case, 0.05)
        self.assertEqual(result.error_type, "timeout")

    def test_process_group_permission_error_falls_back_to_direct_kill(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        with patch(
            "harness.web_fetch.providers.moli.os.killpg",
            side_effect=PermissionError("group signal forbidden"),
        ):
            from harness.web_fetch.providers.moli import MoliWebFetchProvider

            MoliWebFetchProvider._kill(process)
        process.kill.assert_called_once_with()

    def test_cancel_swallows_direct_kill_oserror(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        process.kill.side_effect = OSError("direct signal forbidden")
        provider = self.provider('print("unused")')
        provider._active_process = process
        with patch(
            "harness.web_fetch.providers.moli.os.killpg",
            side_effect=PermissionError("group signal forbidden"),
        ):
            provider.cancel()

    def test_cancel_permission_error_does_not_replace_deadline_timeout(self):
        from harness.web_fetch.call_boundary import fetch_with_deadline

        provider = self.provider("import time\ntime.sleep(10)")
        with patch(
            "harness.web_fetch.providers.moli.os.killpg",
            side_effect=PermissionError("group signal forbidden"),
        ):
            result, worker = fetch_with_deadline(provider, self.case, 0.05)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "timeout")
        worker.join(1)

    def test_url_and_config_validation_before_process(self):
        provider = self.provider('raise AssertionError("should not run")')
        with patch("harness.web_fetch.providers.moli.subprocess.Popen") as process:
            with self.assertRaises(ValueError):
                provider.fetch(EvalCase("x", "x", "file:///etc/passwd", "x", []), 1)
            process.assert_not_called()
        self.assertIn("wait_until", self.provider("", wait_until="invalid").validate())
        self.assertTrue(
            build_provider(
                "moli-webfetch", {"type": "moli_webfetch", "binary": "/nonexistent/moli"}
            ).validate()
        )

    def test_registration_documented(self):
        self.assertIn("moli-webfetch", SPECS)
        self.assertIn("moli-webfetch", PROFILES)

    def test_binary_resolved_once_per_fetch(self):
        provider = self.provider('print("ok")')
        with patch(
            "harness.web_fetch.providers.moli.shutil.which", side_effect=[str(self.binary), None]
        ) as which:
            result = provider.fetch(self.case, 2)
            self.assertTrue(result.ok, repr(result))
            self.assertEqual(which.call_count, 1)
            self.assertEqual(provider.fetch(self.case, 2).error_type, "configuration")

    def test_binary_replacement_fails_closed_against_pinned_hash(self):
        provider = self.provider('print("first")')
        self.assertEqual(provider.validate(), [])
        first = provider.fetch(self.case, 2)
        self.provider('print("second")')  # Replace the executable at the same path.
        errors = provider.validate()
        self.assertTrue(any(error.startswith("Moli binary SHA-256 ") for error in errors))
        second = provider.fetch(self.case, 2)
        self.assertFalse(second.ok)
        self.assertEqual(second.error_type, "configuration")
        self.assertEqual(first.metadata["reported_version"], "moli 1.1.4")

    def test_wrong_reported_version_fails_closed(self):
        provider = self.provider('print("unused")', expected_reported_version="moli 9.9.9")
        self.assertIn("Moli reported version moli 9.9.9", provider.validate())

    def test_fetch_validates_identity_when_preflight_was_not_called(self):
        provider = self.provider('print("ok")', expected_reported_version="moli 9.9.9")
        result = provider.fetch(self.case, 2)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")
        self.assertIn("Moli reported version", result.error_message)

    def test_failed_revalidation_clears_prior_identity(self):
        provider = self.provider('print("ok")')
        self.assertEqual(provider.validate(), [])
        provider.config["expected_reported_version"] = "moli 9.9.9"
        self.assertIn("Moli reported version moli 9.9.9", provider.validate())
        self.assertFalse(hasattr(provider, "_implementation_identity"))

    def test_runtime_identity_records_version_and_hash_without_local_path(self):
        provider = self.provider('print("ok")')
        self.assertEqual(provider.validate(), [])
        self.assertEqual(provider._implementation_identity, {
            "reported_version": "moli 1.1.4",
            "binary_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
        })
        self.assertNotIn(str(self.binary), repr(provider._implementation_identity))

    def test_disappearance_and_permission_errors_become_records(self):
        provider = self.provider('print("ok")')
        with patch(
            "harness.web_fetch.providers.moli.open", side_effect=FileNotFoundError("removed")
        ):
            self.assertIn("binary (unreadable)", provider.validate())
        with patch(
            "harness.web_fetch.providers.moli.subprocess.Popen",
            side_effect=PermissionError("not executable"),
        ):
            self.assertEqual(provider.fetch(self.case, 2).error_type, "configuration")
