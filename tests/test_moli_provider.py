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
        from harness.web_fetch.scoring import score_attempt
        from harness.web_fetch.metrics import task_passed
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
        with patch("harness.web_fetch.providers.moli.extract_document_text", side_effect=AssertionError("caller conversion")):
            for track, kind, url in [("challenge", "structure", "https://example.com/"),
                    ("risk_control", "body", "https://example.com/"),
                    ("document", "body", "https://example.com/a.docx"),
                    ("document", "body", "https://example.com/a.pdf"),
                    ("document", "body", "https://example.com/viewer.html"),
                    ("image", "resource", "https://example.com/"),
                    ("video", "resource", "https://example.com/"),
                    ("video", "body", "https://example.com/")]:
                result = provider.fetch(self.case_with(track=track, task_kind=kind, url=url), 5)
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
            "assert '--dump' in sys.argv and '--http-max-response-size' in sys.argv\n"
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
            '"--timeout","2000","--http-timeout","2000","--http-connect-timeout","2000",'
            '"--http-max-response-size","20000000","--block-private-networks",'
            '"https://example.com/?a=1&b=2"]\n'
            "assert sys.argv[1:]==expected\n"
            'print("# Example Domain")\n'
            'print("diagnostic only",file=sys.stderr)\n'
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
        self.assertTrue(result.metadata["block_private_networks"])

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

    def test_video_body_uses_generic_answer_free_metadata_extraction(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" in sys.argv and "--dump" not in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "document.title" in expression and "document.body?.innerText" in expression\n'
            'assert "itemprop=\\"author\\"" in expression\n'
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
        self.assertEqual(result.metadata["output"], "visible_text_and_standard_media_metadata")
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
        # Measurement starts after runtime preparation, as in the runner.
        self.assertEqual(provider.validate(), [])
        result = provider.fetch(self.case_with(track="static", task_kind="body"), 2)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "ordinary_markdown")

    def test_challenge_route_uses_one_json_trace_and_converts_final_dom(self):
        envelope = {
            "final_url": "https://example.com/final",
            "status": 200,
            "title": "Catalog",
            "headers": [{"name": "content-type", "value": "text/html"}],
            "redirect_chain": [
                {
                    "from_url": "https://example.com/start",
                    "to_url": "https://example.com/final",
                    "status": 302,
                }
            ],
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
        self.assertEqual(
            result.metadata["network_trace"]["response_header_names"], ["content-type"]
        )
        self.assertIn("_raw_stdout_b64", result.metadata)

    def test_page_identity_uses_same_call_json_for_single_and_multihop_redirects(self):
        from harness.web_fetch.scoring import score_attempt
        from harness.web_fetch.metrics import task_passed
        for hops in (1, 2):
            with self.subTest(hops=hops):
                final='https://example.com/document'
                envelope={'final_url':final,'status':200,'title':'Article',
                          'html':'<main>Complete required body</main>',
                          'redirect_chain':[{'from_url':'https://example.com/redirect',
                                             'to_url':final,'status':302}]*hops}
                provider=self.provider(
                    'import sys\n'
                    'assert sys.argv[sys.argv.index("--dump")+1]=="json"\n'
                    'assert "--trace-network" not in sys.argv\n'
                    'print("DEBUG fetch request complete method=GET url=https://cdn.example.com/app.js final_url=https://cdn.example.com/app.js status=200",file=sys.stderr)\n'
                    f'print({json.dumps(envelope)!r})')
                case=self.case_with(track='risk_control',task_kind='body',
                    source_benchmark={'risk_control':{'navigation_contract':{'requested_url':self.case.url,'observed_final_url':final,'target_language':'en'}}})
                result=provider.fetch(case,10)
                self.assertTrue(result.ok,repr(result))
                self.assertEqual(result.final_url,final)
                self.assertEqual(result.metadata['final_url_source'],'same_call_json')
                self.assertEqual(result.metadata['output'],'final_dom_html+local_markdown')
                self.assertFalse(result.metadata['network_trace_requested'])
                config={'id':'article','url':case.url,'track':'risk_control','task_kind':'body',
                        'source_benchmark':case.source_benchmark,
                        'acceptance_contract':{'version':1,'schema':'body_text','minimum_records':0,
                          'stable_text_anchors':[{'id':'body','alternatives':['Complete required body']}]}}
                metrics=score_attempt(config,result.content.encode(),ok=result.ok,
                                      status_code=result.status_code,final_url=result.final_url)
                self.assertEqual(metrics['page_identity_status'],'verified')
                self.assertTrue(task_passed(result.ok,metrics,'body'))
                wrong=score_attempt(config,result.content.encode(),ok=True,status_code=200,
                                    final_url='https://cdn.example.com/app.js')
                self.assertEqual(wrong['page_identity_status'],'mismatch')
                self.assertFalse(task_passed(True,wrong,'body'))

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

    def test_page_json_runtime_error_is_not_a_serialization_error(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                provider = self.provider(
                    'import sys\n'
                    'print("SSL connect error: connection reset", file=sys.stderr)\n'
                    f'sys.exit({exit_code})\n')
                result = provider.fetch(self.case_with(track="challenge", task_kind="structure"), 10)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_type, 'tool_error' if exit_code else 'invalid_output')
                if exit_code:
                    self.assertIn('SSL connect error', result.error_message)
                self.assertEqual(result.metadata['exit_code'], exit_code)
                self.assertEqual(result.metadata['raw_stdout_sha256'], hashlib.sha256(b'').hexdigest())

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
            'assert "main article" in expression and "const lines = node" in expression\n'
            'expected=["--wait-selector","main article","--wait-until","domcontentloaded",'
            '"--delay-ms","999","--timeout","10000","--http-timeout",'
            '"https://example.com/?a=1&b=2"]\n'
            "assert all(value in sys.argv for value in expected),sys.argv\n"
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

    def test_iframe_waits_for_child_count_and_serializes_frames(self):
        provider = self.provider(
            "import sys\n"
            'assert "--eval" in sys.argv and "--wait-script" in sys.argv\n'
            'assert "frame.contentDocument.querySelectorAll(\\"h3.family-name\\")" in sys.argv[5]\n'
            'assert "--delay-ms" in sys.argv and "3000" in sys.argv\n'
            'assert "--dump" not in sys.argv and "--with-frames" not in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "iframe#iframe" in expression and "const lines = node" in expression\n'
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
            'assert "const lines = node" in expression and "root?.body" in expression\n'
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
        self.assertEqual(result.metadata["output"], "selected_records_or_rendered_body_fallback")
        self.assertEqual(
            result.metadata["wait"]["extraction"],
            "selected_records_or_rendered_body_fallback",
        )

    def test_dynamic_selected_records_are_not_duplicated_by_page_body(self):
        provider = self.provider(
            "import sys\n"
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            "assert \"? records.join('\\\\n\\\\n') : fallback\" in expression\n"
            "assert \"records.join('\\\\n\\\\n') +\" not in expression\n"
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
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "node.parentElement" not in expression\n'
            'assert ")].slice(0, 16)" in expression\n'
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

    def test_dynamic_v2_extracts_declared_cards_without_interactive_controls(self):
        provider = self.provider(
            "import sys\n"
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "main main > div > div" in expression\n'
            'assert ")].slice(0, 16)" in expression\n'
            'assert "node.querySelectorAll(item)" in expression\n'
            'assert "cloneNode" not in expression and "clone.textContent" not in expression\n'
            'assert "values.filter(value => !omitted.has(value))" in expression\n'
            'assert "node.parentElement" not in expression\n'
            'assert "document.body" in expression\n'
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
        self.assertEqual(
            result.metadata["wait"]["extraction_scope"],
            {
                "selector": "main main > div > div",
                "maximum_count": 16,
                "frame_selector": None,
                "exclude_selectors": ["button"],
                "fallback": "body",
            },
        )

    def test_dynamic_v2_missing_iframe_falls_back_to_top_level_body(self):
        provider = self.provider(
            "import sys\n"
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "iframe#results" in expression\n'
            'assert "root?.body || root?.documentElement || document.body" in expression\n'
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

    def test_resource_page_uses_bounded_runtime_url_discovery(self):
        provider = self.provider(
            "import sys\n"
            'assert "--dump" not in sys.argv\n'
            'assert "--eval" in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "[src],[href],[data],[poster],[srcset]" in expression\n'
            'assert "astro-island[component-url]" in expression\n'
            'assert not ({"--image","--video","--media","--resource"} & set(sys.argv))\n'
            'print("{\\"links\\":[\\"https://example.com/movie.mp4\\"]}")'
        )
        result = provider.fetch(self.case_with(track="image", task_kind="resource"), 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "resource_page_runtime")
        self.assertEqual(result.metadata["output"], "structured_resource_urls")
        self.assertIn("movie.mp4", result.content)

    def test_resource_contract_takes_priority_over_page_family(self):
        for track, path in (("challenge", "/page"), ("document", "/document.pdf"),
                            ("document", "/document.docx"), ("document", "/viewer.html"),
                            ("video", "/page"), ("image", "/page")):
            with self.subTest(track=track, path=path):
                provider = self.provider(
                    'import sys\n'
                    'assert "--eval" in sys.argv and "--dump" not in sys.argv\n'
                    'assert "--trace-network" not in sys.argv\n'
                    'expression=sys.argv[sys.argv.index("--eval")+1]\n'
                    'assert "[src],[href],[data],[poster],[srcset]" in expression\n'
                    'print("{\\"links\\":[\\"https://example.com/movie.mp4\\"]}")'
                )
                with patch("harness.web_fetch.providers.moli.require_converter",
                           side_effect=AssertionError("resource discovery must not convert Markdown")):
                    result = provider.fetch(self.case_with(
                        track=track, url="https://example.com" + path, task_kind="resource"), 10)
                self.assertTrue(result.ok, repr(result))
                self.assertEqual(result.metadata["route"], "resource_page_runtime")
                self.assertEqual(result.metadata["output"], "structured_resource_urls")
                self.assertFalse(result.metadata["network_trace_requested"])
                self.assertEqual(json.loads(result.content)["links"], ["https://example.com/movie.mp4"])

    def test_file_contract_never_initializes_page_conversion(self):
        for track in ("challenge", "document", "video", "image"):
            with self.subTest(track=track):
                provider = self.provider(
                    'import sys\n'
                    'assert "--dump" not in sys.argv and "--eval" not in sys.argv\n'
                    'assert "--trace-network" not in sys.argv\n'
                    'sys.stdout.buffer.write(b"fixture-file")'
                )
                with patch("harness.web_fetch.providers.moli.require_converter",
                           side_effect=AssertionError("file delivery must not convert Markdown")):
                    result = provider.fetch(self.case_with(track=track, task_kind="file"), 10)
                self.assertTrue(result.ok, repr(result))
                self.assertEqual(result.files[0]["data"], b"fixture-file")
                self.assertEqual(result.metadata["route"], "raw_file")
                self.assertEqual(result.metadata["output"], "raw_bytes")
                self.assertFalse(result.metadata["network_trace_requested"])

    def test_dynamic_resource_honors_declared_completion(self):
        provider = self.provider(
            'import sys\n'
            'assert sys.argv[sys.argv.index("--wait-selector")+1]=="main article"\n'
            'assert sys.argv.count("--wait-until")==1\n'
            'assert sys.argv[sys.argv.index("--wait-until")+1]=="domcontentloaded"\n'
            'assert sys.argv[sys.argv.index("--delay-ms")+1]=="999"\n'
            'assert sys.argv[sys.argv.index("--timeout")+1]=="3000"\n'
            'assert "[src],[href],[data],[poster],[srcset]" in sys.argv[sys.argv.index("--eval")+1]\n'
            'import json\nprint(json.dumps(dict(links=["https://example.com/image.png"])))'
        )
        result = provider.fetch(self.case_with(
            track="dynamic", task_kind="resource", retrieval_hints={
                "version": 1, "wait_until": "domcontentloaded", "completion": {
                    "selector": "main article", "minimum_count": 1,
                    "timeout_ms": 3000, "delay_ms": 999,
                },
            }), 10)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.metadata["route"], "resource_page_runtime")
        self.assertEqual(result.metadata["wait"]["selector"], "main article")
        self.assertEqual(result.metadata["wait"]["completion_support"], "exact")

    def test_dynamic_file_cannot_discard_valid_dom_completion(self):
        marker = Path(self.directory.name) / "fetch-executed"
        provider = self.provider(
            f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n'
            'print("unexpected output")'
        )
        result = provider.fetch(self.case_with(
            track="dynamic", task_kind="file", retrieval_hints={
                "version": 1, "wait_until": "load", "completion": {
                    "selector": "main", "minimum_count": 1,
                    "timeout_ms": 3000, "delay_ms": 0,
                },
            }), 10)
        self.assertFalse(marker.exists())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")
        self.assertIn("DOM completion", result.error_message)

    def test_resource_route_cannot_discard_declared_text_extraction(self):
        marker = Path(self.directory.name) / "fetch-executed"
        provider = self.provider(
            f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n'
            'print("unexpected output")'
        )
        result = provider.fetch(self.case_with(
            track="dynamic", task_kind="resource", retrieval_hints={
                "version": 2, "wait_until": "load", "completion": {
                    "selector": "main", "minimum_count": 1,
                    "timeout_ms": 3000, "delay_ms": 0,
                },
                "extraction": {"selector": "article", "maximum_count": 2,
                               "exclude_selectors": [], "fallback": "empty"},
            }), 10)
        self.assertFalse(marker.exists())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")
        self.assertIn("Declared text extraction", result.error_message)

    def test_scoring_answers_do_not_change_execution(self):
        provider = self.provider('import json, sys\nprint(json.dumps(sys.argv[1:]))')
        original = provider.fetch(self.case, 2)
        changed = provider.fetch(self.case_with(
            id="unrelated-id", name="Unrelated label", expected_any=["secret-answer"],
            reference_answer="secret-reference", required_facts=["secret-fact"],
            ground_truth_main_content="secret-body", expected_resources=["secret-resource"],
        ), 2)
        self.assertTrue(original.ok, repr(original))
        self.assertTrue(changed.ok, repr(changed))
        self.assertEqual(original.content, changed.content)
        self.assertEqual(original.metadata["route"], changed.metadata["route"])

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
        self.assertTrue(result.ok)  # The request completed; the blank PDF still fails task scoring.
        self.assertIsNone(result.error_type)
        self.assertEqual(result.metadata["document_extractor"], "pypdf")
        self.assertEqual(result.metadata["dump"], None)
        self.assertEqual(result.metadata["route"], "raw_document")
        self.assertEqual(result.metadata["output"], "raw_document_bytes+local_text")
        self.assertIn("_raw_stdout_b64", result.metadata)

    def test_docx_runtime_fetch_extracts_valid_ooxml_and_discloses_second_request(self):
        from dataclasses import replace

        document_bytes = b"PK\x03\x04fixture"
        encoded = base64.b64encode(document_bytes).decode("ascii")
        provider = self.provider(
            "import json,sys\n"
            'assert "--eval" in sys.argv and "--dump" not in sys.argv\n'
            'expression=sys.argv[sys.argv.index("--eval")+1]\n'
            'assert "fetch(location.href" in expression and "arrayBuffer" in expression\n'
            f'print(json.dumps({{"kind":"document_base64","source":'
            f'"https://example.com/demo.docx","status":200,'
            f'"content_type":"application/vnd.openxmlformats-officedocument.'
            f'wordprocessingml.document","data":"{encoded}"}}))'
        )
        document = replace(
            self.case,
            url="https://example.com/demo.docx",
            track="document",
        )
        with patch(
            "harness.web_fetch.providers.moli.extract_document_text",
            return_value=("DOCX body", "python-docx"),
        ):
            result = provider.fetch(document, 2)
        self.assertTrue(result.ok, repr(result))
        self.assertEqual(result.content, "DOCX body")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.metadata["route"], "document_runtime_fetch")
        self.assertEqual(result.metadata["output"], "runtime_document_base64+local_text")
        self.assertTrue(result.metadata["document_runtime_second_http_fetch"])
        self.assertEqual(base64.b64decode(result.metadata["_raw_stdout_b64"]), document_bytes)

    def test_docx_runtime_fetch_rejects_non_ooxml_envelope_and_preserves_evidence(self):
        from dataclasses import replace

        raw = b'{"kind":"document_base64","data":"bm90LW9veG1s"}'
        provider = self.provider(f"import sys\nsys.stdout.buffer.write({raw!r})")
        document = replace(
            self.case,
            url="https://example.com/demo.docx",
            track="document",
        )
        result = provider.fetch(document, 2)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "invalid_output")
        self.assertEqual(result.content, "")
        self.assertEqual(result.metadata["raw_stdout_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(base64.b64decode(result.metadata["_raw_stdout_b64"]), raw)

    def test_docx_runtime_local_extraction_must_finish_inside_call_budget(self):
        import time

        encoded = base64.b64encode(b"PK\x03\x04fixture").decode("ascii")
        provider = self.provider(
            f'import json\nprint(json.dumps({{"kind":"document_base64","data":"{encoded}"}}))'
        )
        document = self.case_with(url="https://example.com/demo.docx", track="document")

        real_clock = time.monotonic
        for extraction_seconds, expected_ok in ((0.1, True), (11, False)):
            with self.subTest(extraction_seconds=extraction_seconds):
                elapsed = [0.0]

                def extract(_raw):
                    elapsed[0] += extraction_seconds
                    return "extracted content", "fixture"

                clock = SimpleNamespace(monotonic=lambda: real_clock() + elapsed[0])
                with (
                    patch("harness.web_fetch.providers.moli.time", clock),
                    patch("harness.web_fetch.providers.moli.extract_document_text",
                          side_effect=extract) as extractor,
                ):
                    result = provider.fetch(document, 10)
                extractor.assert_called_once()
                self.assertEqual(result.ok, expected_ok)
                if expected_ok:
                    self.assertEqual(result.content, "extracted content")
                else:
                    self.assertEqual(result.error_type, "timeout")
                    self.assertIn("runtime extraction", result.error_message)

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
            self.case_with(
                url="https://mozilla.github.io/pdf.js/web/viewer.html", track="document"
            ),
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
                result = self.provider(script, **config).fetch(self.case, 2)
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

    def test_zero_exit_empty_markdown_is_request_success(self):
        result = self.provider('print(" ")').fetch(self.case, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.content.strip(), "")
        self.assertIsNone(result.error_type)

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
