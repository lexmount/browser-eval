import base64
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.moli import _diagnostic_main_document_url
from harness.web_fetch.report_interfaces import SPECS
from harness.web_fetch.report_methods import PROFILES


@unittest.skipUnless(os.name == "posix", "executable fixture uses POSIX shebang")
class MoliTests(unittest.TestCase):
    def test_effective_main_document_url_comes_from_same_call_debug_log(self):
        requested = "https://example.com/start?q=1"
        stderr = (
            "DEBUG fetch request complete method=GET "
            f"url={requested} final_url=https://example.com/final status=200 transfer_size=42\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/app.js "
            "final_url=https://cdn.example.com/app.js status=200 transfer_size=7\n"
        )
        self.assertEqual(
            _diagnostic_main_document_url(stderr, requested), "https://example.com/final"
        )

    def test_document_request_identity_preserves_port_and_encoded_separators(self):
        for requested, logged, matches in (
            ("https://example.com:8443/start", "https://example.com/start", False),
            ("https://example.com:0/start", "https://example.com/start", False),
            ("https://example.com/a%2Fb", "https://example.com/a/b", False),
            ("https://example.com/a%2fb", "https://example.com/a%2Fb", True),
            ("https://example.com:443/start", "https://example.com/start", True),
            ("https://example.com/新闻", "https://example.com/%E6%96%B0%E9%97%BB", True),
            ("https://example.com/a b", "https://example.com/a%20b", False),
            ("https://example.com/start", "https://[invalid/", False),
        ):
            with self.subTest(requested=requested, logged=logged):
                stderr = (f"DEBUG fetch request complete method=GET url={logged} "
                          "final_url=https://example.com/document status=200\n")
                self.assertEqual(_diagnostic_main_document_url(stderr, requested),
                                 "https://example.com/document" if matches else None)

    def test_invalid_final_url_is_unavailable_instead_of_raising(self):
        for final_url in ("https://[invalid/", "https://example.com:invalid/", "https://example.com:99999/"):
            with self.subTest(final_url=final_url):
                stderr = ("DEBUG fetch request complete method=GET url=https://example.com/start "
                          f"final_url={final_url} status=200\n")
                self.assertIsNone(_diagnostic_main_document_url(stderr, "https://example.com/start"))

    def test_effective_main_document_url_rejects_unowned_redirect_hops(self):
        stderr = (
            "DEBUG fetch request complete method=GET url=https://example.com/start "
            "final_url=https://example.com/start status=302 transfer_size=42\n"
            "DEBUG fetch request complete method=GET url=https://login.example.com/challenge "
            "final_url=https://login.example.com/challenge status=200 transfer_size=7\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/app.js "
            "final_url=https://cdn.example.com/app.js status=200 transfer_size=7\n"
        )
        self.assertIsNone(_diagnostic_main_document_url(stderr, "https://example.com/start"))

    def test_effective_main_document_url_rejects_unowned_late_redirect_hops(self):
        stderr = (
            "DEBUG fetch request complete method=GET url=https://example.com/start "
            "final_url=https://example.com/ status=200 transfer_size=42\n"
            "DEBUG following pending location navigation asynchronously stage=Load "
            "url=https://challenge.example.com/check?redir=https%3A%2F%2Fexample.com%2Fnext\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/app.css "
            "final_url=https://cdn.example.com/app.css status=200 transfer_size=7\n"
            "DEBUG fetch request complete method=GET "
            "url=https://challenge.example.com/check?redir=https%3A%2F%2Fexample.com%2Fnext "
            "final_url=https://challenge.example.com/check?redir=https%3A%2F%2Fexample.com%2Fnext "
            "status=302 transfer_size=0\n"
            "DEBUG fetch request complete method=GET url=https://example.com/next "
            "final_url=https://example.com/next status=302 transfer_size=0\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/theme.css "
            "final_url=https://cdn.example.com/theme.css status=200 transfer_size=7\n"
            "DEBUG committed main navigation before replacement realm bootstrap page_id=1\n"
            "DEBUG fetch request complete method=GET url=https://example.com/document "
            "final_url=https://example.com/document status=200 transfer_size=42\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/dynamic-asset "
            "final_url=https://cdn.example.com/dynamic-asset status=200 transfer_size=7\n"
            "DEBUG published standalone replacement Document page_id=1\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/app.js "
            "final_url=https://cdn.example.com/app.js status=200 transfer_size=7\n"
        )
        self.assertIsNone(_diagnostic_main_document_url(stderr, "https://example.com/start"))

    def test_effective_main_document_url_does_not_infer_an_unfetched_target(self):
        stderr = (
            "DEBUG fetch request complete method=GET url=https://example.com/start "
            "final_url=https://example.com/ status=200 transfer_size=42\n"
            "DEBUG following pending location navigation asynchronously stage=Load "
            "url=https://challenge.example.com/check?redir=https%3A%2F%2Fexample.com%2Fnext\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/app.css "
            "final_url=https://cdn.example.com/app.css status=200 transfer_size=7\n"
        )
        self.assertEqual(
            _diagnostic_main_document_url(stderr, "https://example.com/start"),
            "https://example.com/",
        )

    def test_effective_main_document_url_accepts_ordinary_committed_navigation(self):
        stderr = (
            "DEBUG fetch request complete method=GET url=https://example.com/start "
            "final_url=https://example.com/start status=200\n"
            "DEBUG following pending location navigation asynchronously stage=Load "
            "url=https://example.com/next\n"
            "DEBUG fetch request complete method=GET url=https://example.com/next "
            "final_url=https://example.com/document status=200\n"
            "DEBUG committed main navigation before replacement realm bootstrap page_id=1\n"
            "DEBUG published standalone replacement Document page_id=1\n"
            "DEBUG fetch request complete method=GET url=https://cdn.example.com/asset "
            "final_url=https://cdn.example.com/asset status=200\n"
        )
        self.assertEqual(_diagnostic_main_document_url(stderr, "https://example.com/start"),
                         "https://example.com/document")
        uncommitted = stderr.replace(
            "DEBUG committed main navigation before replacement realm bootstrap page_id=1\n", "")
        self.assertEqual(_diagnostic_main_document_url(uncommitted, "https://example.com/start"),
                         "https://example.com/start")

    def test_committed_nonredirect_post_and_get_preserve_same_document_identity(self):
        from lexbench_webfetch.scoring import score_attempt
        from lexbench_webfetch.metrics import task_passed
        case={'url':'https://example.com/start','track':'risk_control','task_kind':'body',
              'source_benchmark':{'risk_control':{'navigation_contract':{
                  'requested_url':'https://example.com/start','observed_final_url':'https://example.com/document'}}},
              'acceptance_contract':{'version':1,'schema':'body_text','minimum_records':0,
                'stable_text_anchors':[{'id':'body','alternatives':['Complete required body']}]}}
        for method in ('GET','POST'):
            with self.subTest(method=method):
                log=('DEBUG fetch request complete method=GET url=https://example.com/start final_url=https://example.com/start status=200\n'
                     'DEBUG following pending location navigation asynchronously stage=Load url=https://example.com/document\n'
                     'DEBUG committed main navigation before replacement realm bootstrap page_id=1\n'
                     f'DEBUG fetch request complete method={method} url=https://example.com/document final_url=https://example.com/document status=200\n')
                final=_diagnostic_main_document_url(log,case['url'])
                self.assertEqual(final,'https://example.com/document')
                metrics=score_attempt(case,b'Complete required body',ok=True,final_url=final)
                self.assertEqual(metrics['page_identity_status'],'verified')
                self.assertTrue(task_passed(True,metrics,'body'))

    def test_resource_route_enumerates_bounded_same_origin_frame_documents(self):
        from harness.web_fetch.task_outputs import RESOURCE_PAGE_EXPRESSION

        self.assertIn("frame.contentDocument", RESOURCE_PAGE_EXPRESSION)
        self.assertIn("roots.length < 16", RESOURCE_PAGE_EXPRESSION)
        self.assertNotIn("pic_trulli", RESOURCE_PAGE_EXPRESSION)

    def test_cancellation_between_popen_and_handle_registration_is_latched(self):
        import subprocess

        provider = self.provider("import time\ntime.sleep(20)")
        self.assertEqual(provider.validate(), [])
        original = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            process = original(*args, **kwargs)
            if args and args[0][0] == str(self.binary):
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
            "    raise SystemExit(0)\n" + script
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

    def test_native_families_never_use_caller_scripts_or_conversion(self):
        provider = self.provider(
            "assert '--eval' not in sys.argv and '--wait-script' not in sys.argv\n"
            "assert '--trace-network' not in sys.argv\n"
            "if sys.argv[-1].endswith(('.pdf', '.docx')):\n"
            "    assert '--dump' not in sys.argv\n"
            "else:\n"
            "    assert sys.argv[sys.argv.index('--dump') + 1] in ('markdown', 'html')\n"
            "print('native output')\n", uniform_native_parameters=True)
        with patch("harness.web_fetch.document_text.extract_document_text", side_effect=AssertionError("caller conversion")):
            for track, kind, url, media in [("challenge", "structure", "https://example.com/", "html_page"),
                    ("risk_control", "body", "https://example.com/", "html_page"),
                    ("document", "body", "https://example.com/a.docx", "direct_docx"),
                    ("document", "body", "https://example.com/a.pdf", "direct_pdf"),
                    ("document", "body", "https://example.com/viewer.html", "html_page"),
                    ("image", "resource", "https://example.com/", "html_page"),
                    ("video", "resource", "https://example.com/", "html_page"),
                    ("video", "body", "https://example.com/", "html_page")]:
                result = provider.fetch(self.case_with(track=track, task_kind=kind, url=url,
                    source_benchmark={"input_media_kind": media}), 5)
                self.assertTrue(result.ok, result.error_message)
                if url.endswith((".pdf", ".docx")):
                    self.assertEqual(result.content, "")
                    self.assertEqual(result.files[0]["data"].strip(), b"native output")
                else:
                    self.assertEqual(result.content.strip(), "native output")
                    self.assertEqual(result.metadata["dump"], "html" if kind == "resource" else "markdown")

    def test_fixed_native_cli_omits_internal_timers(self):
        provider = self.provider(
            "import sys\n"
            "assert not {'--timeout', '--http-timeout', '--http-connect-timeout', '--eval'} & set(sys.argv)\n"
            "assert sys.argv[sys.argv.index('--dump') + 1] == 'markdown'\n"
            "assert '--layout' not in sys.argv and '--with-frames' not in sys.argv\n"
            "assert '--dump' in sys.argv and '--http-max-response-size' not in sys.argv\n"
            "print('complete native answer')\n",
            uniform_native_parameters=True,
        )
        self.assertEqual(provider.validate(), [])
        case = self.case_with(source_benchmark={"risk_control": {"navigation_contract": {
            "requested_url": self.case.url, "observed_final_url": self.case.url}}})
        result = provider.fetch(case, 45)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "complete native answer\n")
        self.assertIsNone(result.metadata["http_connect_timeout_ms"])

    def test_cli_contract_and_diagnostics(self):
        provider = self.provider(
            "import sys\n"
            'expected=["fetch","--dump","markdown","--wait-until","done",'
            '"--timeout","40500","--http-timeout","40500",'
            '"--http-connect-timeout","40500",'
            '"--block-private-networks",'
            '"https://example.com/?a=1&b=2"]\n'
            "assert sys.argv[1:]==expected\n"
            'print("# Example Domain")\n'
            'print("diagnostic only",file=sys.stderr)\n'
        )
        self.assertEqual(provider.validate(), [])
        result = provider.fetch(self.case, 45)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "# Example Domain\n")
        self.assertIn("diagnostic only", result.metadata["stderr"])
        self.assertIsNone(result.status_code)
        self.assertIsNone(result.final_url)
        self.assertEqual(len(result.metadata["binary_sha256"]), 64)
        self.assertEqual(result.metadata["route"], "ordinary_markdown")
        self.assertEqual(result.metadata["output"], "markdown")
        self.assertEqual(result.metadata["output_contract"], "markdown")
        self.assertEqual(result.metadata["extractor"], "moli_native_markdown")
        self.assertEqual(result.metadata["wait"], {"wait_until": "done"})
        self.assertEqual(result.metadata["http_connect_timeout_ms"], 40_500)
        self.assertTrue(result.metadata["block_private_networks"])

    def test_internal_deadline_preserves_typed_cli_error_before_outer_deadline(self):
        from harness.web_fetch.call_boundary import fetch_with_deadline

        provider = self.provider(
            "import sys, time\n"
            "values = [sys.argv[sys.argv.index(flag) + 1] for flag in "
            "('--timeout', '--http-timeout', '--http-connect-timeout')]\n"
            "assert values == ['250', '250', '250'], values\n"
            "time.sleep(0.27)\n"
            "print('Error: failed to fetch `https://example.com/?a=1&b=2`\\n' "
            "      'Reason: fetch readiness timed out after 250 ms while waiting for load', "
            "file=sys.stderr, flush=True)\n"
            "raise SystemExit(1)\n"
        )
        self.assertEqual(provider.validate(), [])

        result, worker = fetch_with_deadline(provider, self.case, 0.5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(result.metadata["exit_code"], 1)
        self.assertEqual(result.metadata["http_connect_timeout_ms"], 250)
        self.assertEqual(
            result.error_message,
            "Error: failed to fetch `https://example.com/?a=1&b=2`\n"
            "Reason: fetch readiness timed out after 250 ms while waiting for load\n",
        )

    def test_outer_deadline_still_stops_cli_that_ignores_internal_deadline(self):
        from harness.web_fetch.call_boundary import fetch_with_deadline

        provider = self.provider(
            "import sys, time\n"
            "values = [sys.argv[sys.argv.index(flag) + 1] for flag in "
            "('--timeout', '--http-timeout', '--http-connect-timeout')]\n"
            "assert values == ['250', '250', '250'], values\n"
            "time.sleep(5)\n"
            "print('late answer')\n"
        )
        self.assertEqual(provider.validate(), [])

        def kill(process):
            if process is not None and process.poll() is None:
                process.kill()
            return True

        with patch(
            "harness.web_fetch.providers.moli.terminate_owned_process_tree",
            side_effect=kill,
        ):
            result, worker = fetch_with_deadline(provider, self.case, 0.5)

        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(result.content, "")
        self.assertFalse(worker.is_alive())
        self.assertLessEqual(result.metadata["_answer_elapsed_ms"], 550)

    def test_ordinary_markdown_exposes_effective_url_from_debug_stderr(self):
        provider = self.provider(
            "import sys\n"
            'print("# Example Domain")\n'
            'print("DEBUG fetch request complete method=GET url=https://example.com/?a=1&b=2 '
            'final_url=https://example.com/final status=200 transfer_size=42", file=sys.stderr)\n'
        )
        self.assertEqual(provider.validate(), [])
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.final_url, "https://example.com/final")
        self.assertEqual(result.metadata["final_url_source"], "same_call_debug_log")

    def test_effective_url_uses_complete_log_not_public_stderr_preview(self):
        provider = self.provider(
            "import sys\n"
            'print("x"*1_000_100, file=sys.stderr)\n'
            'print("DEBUG fetch request complete method=GET '
            'url=https://example.com/?a=1&b=2 '
            'final_url=https://example.com/final status=200 transfer_size=42", '
            'file=sys.stderr)\n'
            'print("# Example Domain")',
            log_level="debug",
            retain_complete_diagnostic_log=True,
            max_diagnostic_bytes=2_000_000,
        )
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok, repr(result))
        self.assertTrue(result.metadata["stderr_truncated"])
        self.assertNotIn("fetch request complete", result.metadata["stderr"])
        self.assertEqual(result.final_url, "https://example.com/final")
        self.assertEqual(result.metadata["final_url_source"], "same_call_debug_log")
        complete = base64.b64decode(result.metadata["_diagnostic_log_b64"], validate=True)
        self.assertIn(b"fetch request complete", complete)

    def test_binary_path_expands_environment_variable(self):
        provider = self.provider('print("answer")')
        provider.config["binary"] = "$RISK_MOLI_TEST_BINARY"
        with patch.dict(os.environ, {"RISK_MOLI_TEST_BINARY": str(self.binary)}):
            self.assertEqual(provider.validate(), [])
            self.assertEqual(Path(provider._resolve_binary()), self.binary)

    def test_stderr_capture_exceeds_legacy_4096_without_silent_truncation(self):
        provider = self.provider(
            "import sys\nprint('x'*5000,file=sys.stderr)\nprint('answer')",
            max_bytes=10_000,
        )
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok)
        self.assertGreater(len(result.metadata["stderr"]), 4096)
        self.assertGreater(result.metadata["stderr_bytes"], 4096)
        self.assertFalse(result.metadata["stderr_truncated"])

    def test_debug_logging_is_passed_and_complete_raw_stderr_is_bound(self):
        provider = self.provider(
            "import sys\n"
            'assert sys.argv[sys.argv.index("--log-level")+1]=="debug"\n'
            'print("first debug line",file=sys.stderr)\n'
            'print("second debug line",file=sys.stderr)\n'
            'print("answer")',
            log_level="debug",
            retain_complete_diagnostic_log=True,
            max_diagnostic_bytes=100_000_000,
            trace_network=False,
        )
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok)
        raw = base64.b64decode(result.metadata["_diagnostic_log_b64"], validate=True)
        self.assertEqual(raw, b"first debug line\nsecond debug line\n")
        self.assertEqual(result.metadata["diagnostic_log_bytes"], len(raw))
        self.assertEqual(
            result.metadata["diagnostic_log_sha256"], hashlib.sha256(raw).hexdigest()
        )
        self.assertFalse(result.metadata["diagnostic_log_truncated"])
        self.assertTrue(result.metadata["diagnostic_log_process_completed"])

    def test_native_dynamic_uses_markdown_without_generated_wait_or_eval(self):
        case = EvalCase("native-dynamic", "Native dynamic page", "https://example.invalid/",
                        "dynamic", [], track="dynamic", acceptance_contract={
                            "version": 1, "schema": "body_text", "minimum_records": 0,
                            "stable_text_anchors": [{"id": "identity", "alternatives": ["native answer"]}]})
        provider = self.provider(
            "import sys\n"
            "assert '--eval' not in sys.argv and '--wait-selector' not in sys.argv\n"
            "assert sys.argv[sys.argv.index('--dump')+1]=='markdown'\n"
            "print('native answer')",
            log_level="debug",
        )
        result = provider.fetch(case, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["route"], "dynamic_native_markdown")

    def test_native_wait_override_applies_to_every_page_in_a_family(self):
        for fixed_native in (False, True):
            with self.subTest(fixed_native=fixed_native):
                self._assert_family_wait_override(fixed_native)

    def _assert_family_wait_override(self, fixed_native):
        provider = self.provider(
            "import sys\n"
            "url = sys.argv[-1]\n"
            "wait = sys.argv[sys.argv.index('--wait-until') + 1]\n"
            "assert wait == ('domstable' if '/risk-' in url else 'done')\n"
            "assert '--wait-selector' not in sys.argv and '--wait-script' not in sys.argv\n"
            "assert '--eval' not in sys.argv and '--delay-ms' not in sys.argv\n"
            "print('native page body')\n",
            wait_until_by_track={"risk_control": "domstable"},
            uniform_native_parameters=fixed_native,
        )
        for index in range(2):
            with self.subTest(index=index):
                result = provider.fetch(self.case_with(
                    id=f"risk-{index}", track="risk_control",
                    url=f"https://example.com/risk-{index}",
                ), 2)
                self.assertTrue(result.ok, repr(result))
                self.assertEqual(result.metadata["wait"]["scope"], "risk_control_task_family")
                self.assertEqual(result.metadata["wait_until"], "domstable")
        result = provider.fetch(self.case_with(track="web"), 2)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["wait_until"], "done")

    def test_wait_override_rejects_case_specific_and_invalid_settings(self):
        for overrides in ({"risk-123": "domstable"}, {"risk_control": "invalid"},
                          {"risk_control": []}, ["risk_control"]):
            with self.subTest(overrides=overrides):
                provider = self.provider("raise AssertionError('must not execute')",
                                         wait_until_by_track=overrides)
                self.assertIn("wait_until_by_track", provider.validate())

    def test_network_trace_cannot_change_markdown_scoring_route(self):
        provider = self.provider('raise AssertionError("should not run")', trace_network=True)
        self.assertIn(
            "trace_network (requires JSON output and is not allowed for Markdown scoring)",
            provider.validate(),
        )

    def test_private_network_guard_exception_is_scoped_to_allowlisted_host(self):
        provider = self.provider(
            'import sys\nassert "--block-private-networks" not in sys.argv\nprint("allowlisted")',
            private_network_allowlist_hosts=["example.com"],
        )
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok, repr(result))
        self.assertTrue(result.metadata["block_private_networks"])
        self.assertTrue(result.metadata["private_network_guard_exception"])
        self.assertEqual(result.metadata["private_network_guard_exception_host"], "example.com")

    def test_private_network_guard_stays_on_for_unknown_host(self):
        provider = self.provider(
            'import sys\nassert "--block-private-networks" in sys.argv\nprint("guarded")',
            private_network_allowlist_hosts=["allowed.example"],
        )
        # Measurement starts after runtime preparation, as in the runner.
        self.assertEqual(provider.validate(), [])
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok, repr(result))
        self.assertFalse(result.metadata["private_network_guard_exception"])

    def test_video_body_uses_native_markdown_without_metadata_projection(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
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
        self.assertEqual(result.metadata["route"], "ordinary_markdown")
        self.assertEqual(result.metadata["output"], "markdown")
        self.assertNotIn("output_projection_sha256", result.metadata)

    def test_non_video_body_keeps_ordinary_markdown_route(self):
        provider = self.provider(
            "import sys\n"
            'assert "--dump" in sys.argv and "markdown" in sys.argv\n'
            'assert "--eval" not in sys.argv\n'
            'print("ordinary body")'
        )
        # Measurement starts after runtime preparation, as in the runner.
        self.assertEqual(provider.validate(), [])
        result = provider.fetch(self.case_with(track="static", task_kind="body"), 2)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "ordinary_markdown")

    def test_popular_site_uses_native_markdown_without_local_conversion(self):
        provider = self.provider(
            'import sys\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'assert "--trace-network" not in sys.argv and "--eval" not in sys.argv\n'
            'print("# Native catalog\\n\\n[Real product](/item/1)")')
        self.assertEqual(provider.validate(), [])
        with patch("harness.web_fetch.providers.moli.subprocess.run", side_effect=AssertionError("Local converter must not run")):
            result = provider.fetch(self.case_with(track="challenge", task_kind="structure"), 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.content, "# Native catalog\n\n[Real product](/item/1)\n")
        self.assertEqual(result.metadata["route"], "ordinary_markdown")
        self.assertEqual(result.metadata["output"], "markdown")
        self.assertFalse(result.metadata["network_trace_requested"])

    def test_required_final_url_never_switches_native_markdown_to_json(self):
        from lexbench_webfetch.scoring import score_attempt
        from lexbench_webfetch.metrics import task_passed
        for supplied_url in (None, 'https://example.com/document'):
            with self.subTest(final_url=supplied_url):
                final='https://example.com/document'
                log = (f'DEBUG fetch request complete method=GET url={self.case.url} final_url={final} status=200' if supplied_url else '')
                provider=self.provider(
                    'import sys\n'
                    'assert sys.argv[sys.argv.index("--dump")+1]=="markdown"\n'
                    'assert "--trace-network" not in sys.argv\n'
                    f'print({log!r},file=sys.stderr)\n'
                    'print("Complete required body")')
                case=self.case_with(track='risk_control',task_kind='body',
                    source_benchmark={'risk_control':{'navigation_contract':{'requested_url':self.case.url,'observed_final_url':final,'target_language':'en'}}})
                result=provider.fetch(case,10)
                self.assertTrue(result.ok,repr(result))
                self.assertEqual(result.final_url,supplied_url)
                self.assertEqual(result.content.strip(),'Complete required body')
                self.assertEqual(result.metadata['route'],'ordinary_markdown')
                self.assertEqual(result.metadata['output'],'markdown')
                config={'id':'article','url':case.url,'track':'risk_control','task_kind':'body',
                        'source_benchmark':case.source_benchmark,
                        'acceptance_contract':{'version':1,'schema':'body_text','minimum_records':0,
                          'stable_text_anchors':[{'id':'body','alternatives':['Complete required body']}]}}
                metrics=score_attempt(config,result.content.encode(),ok=result.ok,
                                      status_code=result.status_code,final_url=result.final_url)
                self.assertTrue(task_passed(result.ok,metrics,'body'))

    def test_legacy_multihop_log_does_not_select_interleaved_resource(self):
        log=(
            'DEBUG fetch request complete method=GET url=https://example.com/start final_url=https://example.com/start status=200\n'
            'DEBUG following pending location navigation asynchronously stage=Load url=https://example.com/check?redir=/next\n'
            'DEBUG fetch request complete method=GET url=https://example.com/check?redir=/next final_url=https://example.com/check?redir=/next status=302\n'
            'DEBUG fetch request complete method=GET url=https://example.com/next final_url=https://example.com/next status=302\n'
            'DEBUG committed main navigation before replacement realm bootstrap page_id=1\n'
            'DEBUG fetch request complete method=GET url=https://cdn.example.com/app.js final_url=https://cdn.example.com/app.js status=200\n'
            'DEBUG fetch request complete method=GET url=https://example.com/document final_url=https://example.com/document status=200\n'
            'DEBUG published standalone replacement Document page_id=1\n')
        self.assertIsNone(_diagnostic_main_document_url(log,'https://example.com/start'))

    def test_popular_site_native_http_error_keeps_body_but_request_fails(self):
        provider = self.provider('print("**Error:** Forbidden\\n\\n**403**: Forbidden")')
        result = provider.fetch(self.case_with(track="challenge", task_kind="structure"), 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "api_error")
        self.assertEqual(result.status_code, 403)
        self.assertIn("Forbidden", result.content)

    def test_native_document_http_status_preserves_markdown_and_status_scope(self):
        request = self.case.url
        for name, log, expected_status in (
            ("main-service-failure", f"DEBUG fetch request complete method=GET url={request} "
             f"final_url={request} status=503", 503),
            ("main-ok-subresource-service-failure", f"DEBUG fetch request complete method=GET url={request} "
             f"final_url={request} status=200\nDEBUG fetch request complete method=GET "
             "url=https://cdn.example.com/app.js final_url=https://cdn.example.com/app.js "
             "status=503", 200),
            ("unowned-redirect-service-failure", f"DEBUG fetch request complete method=GET url={request} "
             f"final_url={request} status=302\nDEBUG fetch request complete method=GET "
             "url=https://cdn.example.com/app.js final_url=https://cdn.example.com/app.js "
             "status=503", None),
            ("subresource-service-failure", "DEBUG fetch request complete method=GET "
             "url=https://cdn.example.com/app.js final_url=https://cdn.example.com/app.js "
             "status=503", None),
            ("stale-document-response", f"DEBUG fetch request complete method=GET url={request} "
             f"final_url={request} status=503\n"
             "DEBUG detached committed main WindowProxy for replacement context page_id=1", None),
            ("ambiguous-same-url", (f"DEBUG fetch request complete method=GET url={request} "
             f"final_url={request} status=503\n") * 2, None),
            ("console-status-prose", "INFO console.log=DEBUG fetch request complete method=GET "
             f"url={request} final_url={request} status=503", None),
        ):
            with self.subTest(name=name):
                body = "# Service documentation\nHTTP 503 and timeout are described here."
                provider = self.provider(f"print({body!r})\nprint({log!r}, file=sys.stderr)\n")
                result = provider.fetch(self.case, 10)
                self.assertEqual(result.content, body + "\n")
                self.assertEqual(result.status_code, expected_status)
                self.assertEqual(result.ok, expected_status is None or expected_status < 400)

    def test_empty_native_service_failure_preserves_http_status(self):
        log = (f"DEBUG fetch request complete method=GET url={self.case.url} "
               f"final_url={self.case.url} status=503")
        result = self.provider(f"print({log!r}, file=sys.stderr)\n").fetch(self.case, 10)
        self.assertEqual(result.content, "")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.error_type, "api_error")

    def test_native_best_effort_deadline_marks_only_empty_delivery(self):
        warning = ("2026-10-15T00:00:00.000000Z  WARN fetch readiness wait timed out; "
                   f"returning best-effort page page_id=1 url={self.case.url} "
                   f"final_url={self.case.url} wait_until=DomStable timeout_ms=40500 "
                   "remaining_ms=30000 error=deadline has elapsed")
        for name, body, log, expected_error, expected_ok in (
            ("empty-native-deadline", "", warning, "timeout", False),
            ("usable-body", "# Expected article\nComplete content.", warning, None, True),
            ("empty-without-deadline", "", "DEBUG ordinary fetch completed", "empty_response", False),
            ("console-deadline-prose", "", "INFO console.log=" + warning, "empty_response", False),
            ("different-document-deadline", "", warning.replace(self.case.url, "https://other.example/"),
             "empty_response", False),
        ):
            with self.subTest(name=name):
                provider = self.provider(f"sys.stdout.write({body!r})\nprint({log!r}, file=sys.stderr)\n")
                result = provider.fetch(self.case, 10)
                self.assertEqual(result.content, body)
                self.assertEqual(result.error_type, expected_error)
                self.assertEqual(result.ok, expected_ok)
                if body:
                    from lexbench_webfetch.scoring import score_attempt
                    from lexbench_webfetch.metrics import task_passed
                    case = self.case_with(min_content_chars=1000)
                    metrics = score_attempt(vars(case), body.encode(), ok=result.ok,
                                            metadata=result.metadata)
                    self.assertFalse(task_passed(result.ok, metrics, case.task_kind))

    def test_native_failures_have_specific_error_types(self):
        scenarios = (
            (
                "dns",
                "Error: failed to fetch `https://example.com/`\n"
                "Reason: failed to resolve example.com:443: failed to lookup address "
                "information: Temporary failure in name resolution",
                "dns",
            ),
            (
                "curl_timeout",
                "Error: failed to fetch `https://example.com/`\n"
                "Reason: curl request failed for https://example.com/ after active=500ms "
                "queued=2ms: [28] Timeout was reached",
                "timeout",
            ),
            (
                "curl_transfer",
                "Error: failed to fetch `https://example.com/`\n"
                "Reason: curl request failed for https://example.com/ after active=500ms "
                "queued=2ms: [56] Failure when receiving data from the peer",
                "network",
            ),
            (
                "certificate",
                "Error: failed to fetch `https://example.com/`\n"
                "Reason: curl request failed for https://example.com/ after active=500ms "
                "queued=2ms: [60] SSL peer certificate or SSH remote key was not OK",
                "tool_error",
            ),
            (
                "content_contract",
                "Error: failed to fetch `https://example.com/`\n"
                "Reason: raw download output only supports automatic output or --dump json",
                "tool_error",
            ),
            (
                "ambiguous",
                "SSL connect error: connection reset",
                "tool_error",
            ),
        )
        for name, stderr, error_type in scenarios:
            with self.subTest(name=name):
                provider = self.provider(
                    "import sys\n"
                    f"print({stderr!r}, file=sys.stderr)\n"
                    "raise SystemExit(1)\n"
                )
                result = provider.fetch(self.case, 2)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_type, error_type)

    def test_native_markdown_runtime_error_keeps_official_stderr(self):
        provider = self.provider('import sys\nprint("SSL connect error: connection reset", file=sys.stderr)\nsys.exit(1)\n')
        result = provider.fetch(self.case_with(track="risk_control", task_kind="body", source_benchmark={"risk_control":{"navigation_contract":{"requested_url":self.case.url,"observed_final_url":"https://example.com/final"}}}), 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, 'tool_error')
        self.assertIn('SSL connect error', result.error_message)
        self.assertEqual(result.metadata['route'], 'ordinary_markdown')

    def test_dynamic_task_without_hints_fails_closed(self):
        provider = self.provider('raise AssertionError("should not run")')
        result = provider.fetch(self.case_with(track="dynamic"), 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")

    def test_dynamic_native_completion_keeps_native_markdown_route(self):
        for task_kind in ("body", "structure"):
            with self.subTest(task_kind=task_kind):
                provider = self.provider(
                    'import sys\n'
                    'assert "--eval" not in sys.argv and "--wait-selector" not in sys.argv\n'
                    'assert "--wait-script" not in sys.argv\n'
                    'assert "--layout" in sys.argv and "--with-frames" in sys.argv\n'
                    'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
                    'assert sys.argv[sys.argv.index("--wait-until")+1] == "domstable"\n'
                    'print("# Native dynamic page")'
                )
                result = provider.fetch(self.case_with(
                    track="dynamic", task_kind=task_kind,
                    retrieval_hints={"version": 3, "mode": "native"}), 10)
                self.assertTrue(result.ok, result.error_message)
                self.assertEqual(result.content, "# Native dynamic page\n")
                self.assertEqual(result.metadata["output"], "markdown")
                self.assertTrue(result.metadata["layout"])
                self.assertTrue(result.metadata["with_frames"])
                self.assertEqual(result.metadata["wait_interface"], "--wait-until")
                self.assertEqual(result.metadata["wait"], {
                    "wait_until": "domstable", "scope": "dynamic_task_family",
                    "strategy": "native_task_family_completion",
                    "layout": {"requested": True, "support": "native",
                               "interface": "--layout", "scope": "dynamic_task_family"},
                    "subdocuments": {"requested": True, "support": "native",
                                     "interface": "--with-frames",
                                     "harness_content_assembly": False,
                                     "scope": "dynamic_task_family"}})

    def test_wait_option_selects_a_supported_native_cli_without_version_branching(self):
        provider = self.provider(
            'import sys\n'
            'assert "--wait-until" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--wait")+1] == "domstable"\n'
            'assert "--layout" in sys.argv and "--with-frames" in sys.argv\n'
            'print("# Native dynamic page")',
            wait_option="wait",
        )
        result = provider.fetch(self.case_with(
            track="dynamic", task_kind="structure",
            retrieval_hints={"version": 3, "mode": "native"}), 10)
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(result.metadata["wait_interface"], "--wait")

    def test_dynamic_family_wait_is_configurable_but_not_case_specific(self):
        provider = self.provider(
            'import sys\n'
            'assert sys.argv[sys.argv.index("--wait-until")+1] == "networkidle"\n'
            'print("# Native body")',
            dynamic_wait_until="networkidle",
        )
        result = provider.fetch(self.case_with(track="dynamic", retrieval_hints={
            "version": 3, "mode": "native"}), 10)
        self.assertTrue(result.ok, result.error_message)
        self.assertEqual(result.metadata["wait"]["scope"], "dynamic_task_family")

    def test_dynamic_task_prefers_predeclared_selector_without_answer_leakage(self):
        provider = self.provider(
            "import sys\n"
            'assert sys.argv[1]=="fetch" and "--eval" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'expected=["--wait-selector","main article","--wait-until","domcontentloaded",'
            '"--timeout","9000","--http-timeout",'
            '"https://example.com/?a=1&b=2"]\n'
            "assert all(value in sys.argv for value in expected),sys.argv\n"
            'assert "--delay-ms" not in sys.argv\n'
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
                "wait_until": "domcontentloaded",
                "completion_support": "exact",
                "output_extraction": "native_markdown",
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
            "wait=sys.argv[5]\n"
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

    def test_frame_completion_waits_without_extracting_or_serializing_content(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" not in sys.argv and "--wait-script" in sys.argv\n'
            'wait=sys.argv[sys.argv.index("--wait-script")+1]\n'
            'assert "frame.contentDocument.querySelectorAll(\\"h3.family-name\\")" in wait\n'
            'assert "--delay-ms" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'assert "--with-frames" not in sys.argv\n'
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
                    "delay_ms": 0,
                },
            },
        )
        result = provider.fetch(case, 45)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["wait"]["strategy"], "frame_selector_count_script")
        self.assertEqual(result.metadata["route"], "dynamic_native_markdown")
        self.assertEqual(result.metadata["output"], "markdown")

    def test_native_dynamic_marker_rejects_a_per_case_contract_before_process(self):
        provider = self.provider('raise AssertionError("should not run")')
        case = self.case_with(id="dynamic-iframe", track="dynamic", retrieval_hints={
            "version": 3, "mode": "native", "contract": "iframe-fourteen-family-headings"})
        with patch("harness.web_fetch.providers.moli.subprocess.Popen") as process:
            result = provider.fetch(case, 10)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")
        self.assertIn("one family marker", result.error_message)
        process.assert_not_called()

    def test_dynamic_completion_only_waits_and_never_extracts_answer_text(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" not in sys.argv and "--wait-script" in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'assert "--with-frames" not in sys.argv\n'
            'wait=sys.argv[sys.argv.index("--wait-script")+1]\n'
            'assert "iframe#results" in wait and "h3.family-name" in wait\n'
            'assert "answer-must-not-leak" not in wait\n'
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
        self.assertEqual(result.metadata["route"], "dynamic_native_markdown")
        self.assertEqual(result.metadata["output"], "markdown")
        self.assertEqual(
            result.metadata["wait"]["output_extraction"],
            "native_markdown",
        )

    def test_legacy_completion_wait_does_not_replace_native_markdown_output(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
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

    def test_dynamic_v1_does_not_guess_ancestors_from_completion_selector(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'wait=sys.argv[sys.argv.index("--wait-script")+1]\n'
            'assert "querySelectorAll(\\"button\\").length >= 16" in wait\n'
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

    def test_dynamic_v2_extraction_contract_does_not_replace_native_markdown(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'wait=sys.argv[sys.argv.index("--wait-script")+1]\n'
            'assert "querySelectorAll(\\"button\\").length >= 16" in wait\n'
            'print("Product\\n$10.00\\nor 2 x$5.00")'
        )
        case = self.case_with(
            track="dynamic",
            retrieval_hints={
                "version": 2,
                "wait_until": "domcontentloaded",
                "completion": {
                    "selector": "button",
                    "minimum_count": 16,
                    "timeout_ms": 10000,
                    "delay_ms": 0,
                },
                "extraction": {
                    "selector": "main main > div > div",
                    "maximum_count": 16,
                    "exclude_selectors": ["button"],
                    "fallback": "body",
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["wait"]["output_extraction"], "native_markdown")

    def test_dynamic_v2_iframe_extraction_is_not_run_by_moli(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" not in sys.argv\n'
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'print("top-level fallback")'
        )
        case = self.case_with(
            track="dynamic",
            retrieval_hints={
                "version": 2,
                "wait_until": "domcontentloaded",
                "completion": {
                    "selector": ".ready",
                    "minimum_count": 1,
                    "timeout_ms": 10000,
                    "delay_ms": 0,
                },
                "extraction": {
                    "frame_selector": "iframe#results",
                    "selector": ".record",
                    "maximum_count": 20,
                    "fallback": "body",
                },
            },
        )
        result = provider.fetch(case, 10)
        self.assertTrue(result.ok, repr(result))

    def test_resource_page_uses_native_markdown_for_common_url_scoring(self):
        provider = self.provider(
            "import sys\n"
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'assert "--eval" not in sys.argv\n'
            'assert "--trace-network" not in sys.argv\n'
            'assert "--with-frames" in sys.argv\n'
            'assert not ({"--image","--video","--media","--resource"} & set(sys.argv))\n'
            'print("[movie](https://example.com/movie.mp4)")'
        )
        result = provider.fetch(self.case_with(track="image", task_kind="resource"), 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "resource_native_markdown")
        self.assertEqual(result.metadata["output"], "native_markdown_resource_urls")
        self.assertEqual(result.metadata["output_contract"], "native_markdown_resource_urls")
        self.assertEqual(
            result.metadata["wait"]["subdocuments"]["scope"],
            "resource_task_family",
        )
        self.assertIn("movie.mp4", result.content)
        from lexbench_webfetch.metrics import evaluate_resources
        scored = evaluate_resources(
            result.content,
            {},
            [{"id": "movie", "kind": "video", "urls": ["https://example.com/movie.mp4"]}],
            base_url="https://example.com/gallery",
        )
        self.assertTrue(scored["resource_pass"])

    def test_video_resource_page_uses_the_same_native_markdown_route(self):
        provider = self.provider(
            "import sys\n"
            'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
            'assert "--trace-network" not in sys.argv and "--with-frames" in sys.argv\n'
            'assert sys.argv[sys.argv.index("--wait-until")+1] == "done"\n'
            'assert "--delay-ms" not in sys.argv\n'
            'print("[stream](https://example.com/movie.m3u8)")'
        )
        result = provider.fetch(self.case_with(track="video", task_kind="resource"), 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "resource_native_markdown")
        self.assertEqual(result.metadata["output"], "native_markdown_resource_urls")
        self.assertEqual(result.metadata["output_contract"], "native_markdown_resource_urls")
        self.assertFalse(result.metadata["network_trace_requested"])
        from lexbench_webfetch.metrics import evaluate_resources
        scored = evaluate_resources(
            result.content,
            {},
            [{"id": "movie", "kind": "video", "urls": ["https://example.com/movie.m3u8"]}],
            base_url="https://example.com/player",
        )
        self.assertTrue(scored["resource_pass"])

    def test_fixed_native_resource_uses_product_timers_and_native_html(self):
        provider = self.provider(
            "import sys\n"
            "assert not {'--timeout', '--http-timeout', '--http-connect-timeout', '--eval'} & set(sys.argv)\n"
            'assert sys.argv[sys.argv.index("--dump")+1] == "html"\n'
            'print("![photo](https://example.com/photo.jpg)")',
            uniform_native_parameters=True,
        )
        result = provider.fetch(self.case_with(track="image", task_kind="resource"), 45)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["internal_timeout_policy"], "native_default")
        self.assertIsNone(result.metadata["http_connect_timeout_ms"])

    def test_document_urls_use_native_text_without_external_parsing(self):
        for url in (
            "https://example.com/paper.pdf",
            "https://example.com/file.docx",
            "https://example.com/document-viewer.html",
        ):
            with self.subTest(url=url):
                provider = self.provider(
                    "import sys\n"
                    'assert sys.argv[sys.argv.index("--dump")+1] == "markdown"\n'
                    'assert "--eval" not in sys.argv\n'
                    'print("Native document text")')
                result = provider.fetch(self.case_with(url=url, track="document"), 2)
                self.assertTrue(result.ok, repr(result))
                self.assertEqual(result.content.strip(), "Native document text")
                self.assertEqual(result.metadata["route"], "ordinary_markdown")
                self.assertNotIn("document_extractor", result.metadata)

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

    def test_answer_capacity_does_not_limit_native_resource_downloads(self):
        for fixed_native in (False, True):
            with self.subTest(fixed_native=fixed_native):
                provider = self.provider(
                    "assert '--http-max-response-size' not in sys.argv\n"
                    "print('short answer')\n",
                    max_bytes=32, uniform_native_parameters=fixed_native,
                )
                result = provider.fetch(self.case, 2)
                self.assertTrue(result.ok, repr(result))
                self.assertEqual(result.content, "short answer\n")
                self.assertIsNone(result.metadata["http_max_response_bytes"])
                self.assertEqual(result.metadata["retained_output_max_bytes"], 32)
                oversized = self.provider(
                    "assert '--http-max-response-size' not in sys.argv\n"
                    "print('answer exceeding the capture limit')\n",
                    max_bytes=32, uniform_native_parameters=fixed_native,
                ).fetch(self.case, 2)
                self.assertFalse(oversized.ok)
                self.assertEqual(oversized.error_type, "output_too_large")
                self.assertEqual(oversized.content, "")

    def test_failure_binary_and_oversized_output(self):
        for script, config, expected, retained in [
            ('import sys\nprint("partial")\nsys.exit(7)', {}, "tool_error", False),
            ('import sys\nsys.stdout.buffer.write(b"\\xff")', {}, "invalid_output", False),
            ('print("abcdef")', {"max_bytes": 3}, "output_too_large", False),
            ('import sys\nprint("abcdef",file=sys.stderr)',
             {"retain_complete_diagnostic_log": True, "max_diagnostic_bytes": 3},
             "diagnostic_log_too_large", True),
        ]:
            with self.subTest(expected=expected):
                provider = self.provider(script, **config)
                self.assertEqual(provider.validate(), [])
                result = provider.fetch(self.case, 2)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_type, expected)
                self.assertEqual("_diagnostic_log_b64" in result.metadata, retained)
                if retained:
                    self.assertEqual(
                        len(base64.b64decode(result.metadata["_diagnostic_log_b64"])),
                        result.metadata["diagnostic_log_bytes"],
                    )
                    self.assertEqual(
                        result.metadata["diagnostic_log_truncated"],
                        expected == "diagnostic_log_too_large",
                    )

    def test_complete_diagnostic_retention_is_opt_in(self):
        result = self.provider(
            'import sys\nprint("diagnostic", file=sys.stderr)\nprint("answer")'
        ).fetch(self.case, 2)
        self.assertTrue(result.ok)
        self.assertNotIn("_diagnostic_log_b64", result.metadata)
        self.assertNotIn("diagnostic_log_sha256", result.metadata)

    def test_diagnostic_only_options_require_explicit_retention(self):
        self.assertIn(
            "max_diagnostic_bytes (requires retain_complete_diagnostic_log)",
            self.provider('print("answer")', max_diagnostic_bytes=10).validate(),
        )
        self.assertIn(
            "local_report_complete_diagnostic_links (requires retain_complete_diagnostic_log)",
            self.provider(
                'print("answer")', local_report_complete_diagnostic_links=True
            ).validate(),
        )

    def test_zero_exit_empty_markdown_is_an_empty_response(self):
        result = self.provider('print(" ")').fetch(self.case, 2)
        self.assertFalse(result.ok)
        self.assertEqual(result.content.strip(), "")
        self.assertEqual(result.error_type, "empty_response")
        self.assertEqual(result.metadata["exit_code"], 0)

    def test_zero_exit_whole_response_forbidden_page_is_request_failure(self):
        result = self.provider(
            'print("**Error:**Forbidden\\n\\n**403**: ForbiddenID:`edge::abc`")'
        ).fetch(self.case, 2)
        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.error_type, "api_error")

    def test_documentation_that_mentions_forbidden_status_is_request_success(self):
        result = self.provider(
            'print("HTTP clients may receive 403: Forbidden. This section explains retries.")'
        ).fetch(self.case, 2)
        self.assertTrue(result.ok, repr(result))
        self.assertIsNone(result.status_code)
        self.assertIsNone(result.error_type)

    def test_outer_deadline_preserves_live_cli_log_and_rejects_late_answer(self):
        from harness.web_fetch.call_boundary import fetch_with_deadline
        from harness.web_fetch.diagnostics import CallDiagnostics
        provider = self.provider(
            'import time\nprint("DEBUG actual fixture CLI started", file=sys.stderr, flush=True)\n'
            'time.sleep(5)\nprint("late answer")',
            uniform_native_parameters=True, log_level="debug", retain_complete_diagnostic_log=True,
        )
        self.assertEqual(provider.validate(), [])
        sink = CallDiagnostics(Path(self.directory.name) / "call.jsonl", "deadline-call")
        provider._call_diagnostics = sink
        result, worker = fetch_with_deadline(provider, self.case, 0.5)
        sink.finish(result)
        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(result.content, "")
        self.assertFalse(worker.is_alive())
        raw = base64.b64decode(result.metadata["_diagnostic_log_b64"])
        self.assertIn(b"DEBUG actual fixture CLI started", raw)
        self.assertNotIn(b"late answer", raw)
        self.assertIn(b"native_cli_start", raw)
    def test_timeout_stops_a_browser_child_in_an_independent_session(self):
        import signal, time
        pid_file = Path(self.directory.name) / 'detached-child.pid'
        marker = Path(self.directory.name) / 'orphan-work'
        child_source = ('import time; from pathlib import Path; time.sleep(2); '
                        + 'Path(' + repr(str(marker)) + ').write_text("orphan")')
        script = ('import subprocess, time\nfrom pathlib import Path\n'
                  + 'child = subprocess.Popen([sys.executable, "-c", ' + repr(child_source)
                  + '], start_new_session=True)\n'
                  + 'Path(' + repr(str(pid_file)) + ').write_text(str(child.pid))\n'
                  + 'time.sleep(10)\n')
        try:
            provider = self.provider(script)
            self.assertEqual(provider.validate(), [])
            result = provider.fetch(self.case, 1)
            self.assertEqual(result.error_type, 'timeout')
            self.assertTrue(pid_file.exists(), 'fixture did not start the independent browser child')
            time.sleep(2.1)
            self.assertFalse(marker.exists(), 'browser child continued physical work after timeout')
        finally:
            if pid_file.exists():
                try:
                    os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_timeout_is_failure(self):
        result = self.provider("import time\ntime.sleep(10)").fetch(self.case, 0.05)
        self.assertEqual(result.error_type, "timeout")

    def test_cancel_does_not_abandon_descendants_when_group_signalling_is_forbidden(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        provider = self.provider('print("unused")')
        provider._active_process = process
        with patch('harness.web_fetch.providers.base.subprocess.run',
                   return_value=SimpleNamespace(stdout='123 1 123\n124 123 124\n')), patch(
                   'harness.web_fetch.providers.base.os.killpg',
                   side_effect=PermissionError('group signal forbidden')):
            provider.cancel()
        process.kill.assert_not_called()
        self.assertIsNone(process.poll(), 'unresolved physical work must remain visible to the deadline boundary')

    def test_cancel_does_not_signal_an_unobservable_process_tree(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        provider = self.provider('print("unused")')
        provider._active_process = process
        with patch('harness.web_fetch.providers.base.subprocess.run',
                   side_effect=OSError('process inventory unavailable')), patch(
                   'harness.web_fetch.providers.base.os.killpg') as signal_group:
            provider.cancel()
        signal_group.assert_not_called()
        process.kill.assert_not_called()

    def test_cancel_permission_error_preserves_timeout_and_unresolved_worker(self):
        from harness.web_fetch.call_boundary import fetch_with_deadline
        provider = self.provider('import time\ntime.sleep(10)')
        worker = None
        try:
            with patch('harness.web_fetch.providers.base.os.killpg',
                       side_effect=PermissionError('group signal forbidden')):
                result, worker = fetch_with_deadline(provider, self.case, .1)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_type, 'timeout')
            self.assertTrue(worker.is_alive(), 'failed cleanup must not hide remaining physical work')
        finally:
            provider.cancel()
            if worker is not None:
                worker.join(2)
                self.assertFalse(worker.is_alive())

    def test_url_and_config_validation_before_process(self):
        provider = self.provider('raise AssertionError("should not run")')
        with patch("harness.web_fetch.providers.moli.subprocess.Popen") as process:
            with self.assertRaises(ValueError):
                provider.fetch(EvalCase("x", "x", "file:///etc/passwd", "x", []), 1)
            process.assert_not_called()
        self.assertIn("wait_until", self.provider("", wait_until="invalid").validate())
        self.assertIn(
            "dynamic_wait_until",
            self.provider("", dynamic_wait_until="invalid").validate(),
        )
        self.assertIn("wait_option", self.provider("", wait_option="invalid").validate())
        self.assertIn(
            "private_network_allowlist_hosts",
            self.provider("", private_network_allowlist_hosts=["https://example.com"]).validate(),
        )
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
        self.assertEqual(
            provider._implementation_identity,
            {
                "reported_version": "moli 1.1.4",
                "binary_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            },
        )
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


class UniformMoliParameterTests(unittest.TestCase):
    def test_uniform_route_rejects_host_specific_guard_exceptions(self):
        from harness.web_fetch.providers.moli import MoliWebFetchProvider
        provider = MoliWebFetchProvider("moli", {
            "uniform_native_parameters": True,
            "private_network_allowlist_hosts": ["specific.example"],
        })
        self.assertIn("private_network_allowlist_hosts cannot vary uniform native parameters",
                      provider._configuration_errors(None))
