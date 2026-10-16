"""Browser integration controls for native whole-page completion."""
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.web_fetch.providers.headless_worker import fetch

BINARY = os.environ.get('CHROMIUM_BINARY')


@unittest.skipUnless(BINARY, 'set CHROMIUM_BINARY to run real browser controls')
class NativePageCompletionTests(unittest.TestCase):
    def test_native_completion_observes_load_body_and_preserves_explicit_early_snapshot(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/slow.gif':
                    time.sleep(.8)
                    body = bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b')
                    kind = 'image/gif'
                else:
                    delayed = self.path == '/delayed'
                    body = ('''<!doctype html><html><body><p>Initial body</p>'''
                        + ('''<img src="/slow.gif"><script>window.addEventListener('load',()=>{
                            document.querySelector('p').textContent='Completed page body';
                            });</script>''' if delayed else '<p>Completed page body</p>')
                        + '</body></html>').encode()
                    kind = 'text/html; charset=utf-8'
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        request = {'engine': 'chromium', 'binary': BINARY, 'timeout': 10,
                   'output_mode': 'markdown', 'task_kind': 'body', 'track': 'dynamic',
                   'retrieval_hints': {'version': 3, 'mode': 'native'},
                   'allow_private_network': True}
        try:
            early = fetch({**request, 'url': origin + '/delayed', 'wait_until': 'domcontentloaded'})
            self.assertTrue(early['ok'], early)
            self.assertNotIn('Completed page body', early['content'])
            self.assertEqual(early['metadata']['navigation_wait_until'],'domcontentloaded')
            native = fetch({**request, 'url': origin + '/delayed'})
            self.assertTrue(native['ok'], native)
            self.assertIn('Completed page body', native['content'])
            self.assertEqual(native['metadata']['navigation_wait_until'],'load')
            fast = fetch({**request, 'url': origin + '/fast'})
            self.assertTrue(fast['ok'], fast)
            self.assertIn('Completed page body', fast['content'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(1)


class RedirectDuringExtractionTests(unittest.TestCase):
    """A redirect may replace a document after navigation completion."""

    def test_read_new_document_after_redirect_without_repeating_navigation(self):
        from unittest.mock import Mock, patch
        from playwright.sync_api import Error
        from harness.web_fetch.providers.headless_worker import _evaluate_document
        page = Mock()
        page.evaluate.side_effect = [
            Error('Page.evaluate: Execution context was destroyed, most likely because of a navigation'),
            '<html><body>Final document body</body></html>',
        ]
        with patch('harness.web_fetch.providers.headless_worker.time.monotonic', return_value=100):
            result = _evaluate_document(page, 'document.documentElement.outerHTML', 101, {})
        self.assertIn('Final document body', result)
        self.assertEqual(page.evaluate.call_count, 2)
        page.wait_for_load_state.assert_called_once_with('domcontentloaded', timeout=1000)
        page.goto.assert_not_called()

    def test_script_error_is_not_retried_as_a_navigation(self):
        from unittest.mock import Mock, patch
        from playwright.sync_api import Error
        from harness.web_fetch.providers.headless_worker import _evaluate_document
        page = Mock()
        page.evaluate.side_effect = Error('Page.evaluate: TypeError: application failed')
        with patch('harness.web_fetch.providers.headless_worker.time.monotonic', return_value=100):
            with self.assertRaisesRegex(Error, 'TypeError: application failed'):
                _evaluate_document(page, 'document.documentElement.outerHTML', 101, {})
        self.assertEqual(page.evaluate.call_count, 1)
        page.wait_for_load_state.assert_not_called()

    def test_repeated_replacement_is_bounded_to_one_recovery(self):
        from unittest.mock import Mock, patch
        from playwright.sync_api import Error
        from harness.web_fetch.providers.headless_worker import _evaluate_document
        page = Mock()
        page.evaluate.side_effect = Error('Page.evaluate: Execution context was destroyed, most likely because of a navigation')
        with patch('harness.web_fetch.providers.headless_worker.time.monotonic', return_value=100):
            with self.assertRaisesRegex(Error, 'Execution context was destroyed'):
                _evaluate_document(page, 'document.documentElement.outerHTML', 101, {})
        self.assertEqual(page.evaluate.call_count, 2)
        self.assertEqual(page.wait_for_load_state.call_count, 1)
        page.goto.assert_not_called()

    def test_expired_budget_does_not_wait_or_repeat_the_read(self):
        from unittest.mock import Mock, patch
        from playwright.sync_api import Error
        from harness.web_fetch.providers.headless_worker import _evaluate_document
        page = Mock()
        page.evaluate.side_effect = Error('Page.evaluate: Execution context was destroyed, most likely because of a navigation')
        with patch('harness.web_fetch.providers.headless_worker.time.monotonic', side_effect=[100,102]):
            with self.assertRaises(TimeoutError):
                _evaluate_document(page, 'document.documentElement.outerHTML', 101, {})
        self.assertEqual(page.evaluate.call_count, 1)
        page.wait_for_load_state.assert_not_called()


class FamilySubdocumentPolicyTests(unittest.TestCase):
    def test_native_frame_flags_follow_family_policy_for_unrelated_urls(self):
        import json
        from unittest.mock import patch
        from lexbench_webfetch.models import EvalCase
        from harness.web_fetch.providers.headless import HeadlessProvider
        provider = HeadlessProvider('lightpanda', {
            'type':'headless_markdown', 'engine':'lightpanda', 'binary':'/lightpanda',
            'include_subdocuments_by_track': {'dynamic':True},
        })
        identity = {'binary_sha256':'fixture', 'reported_version':'1.0.0', 'version_probe_error':None}
        raw = json.dumps({'ok':True, 'content':'native body', 'metadata':{}}).encode()
        for identifier,track,url in [('alpha','dynamic','https://example.com/alpha'),
                                     ('beta','dynamic','https://example.org/beta'),
                                     ('gamma','web','https://example.net/gamma')]:
            case = EvalCase(identifier,identifier,url,'public',[],track=track,
                retrieval_hints={'version':3,'mode':'native'} if track=='dynamic' else {})
            with self.subTest(track=track,url=url), \
                 patch('harness.web_fetch.providers.headless._resolve_binary',return_value='/lightpanda'), \
                 patch.object(provider,'implementation_identity',return_value=identity), \
                 patch.object(provider,'_run_local_worker',return_value=(raw,'',False,0,len(raw),0)) as run:
                result=provider.fetch(case,45)
            self.assertTrue(result.ok)
            request=run.call_args.args[1]
            self.assertEqual(request['include_subdocuments'],track=='dynamic')
            self.assertEqual(request['output_mode'],'markdown')
