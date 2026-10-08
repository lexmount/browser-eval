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
