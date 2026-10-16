"""Local browser deadlines stop physical work without dropping later tasks."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.headless import HeadlessProvider
from harness.web_fetch.runner import run_evaluation


@unittest.skipUnless(os.name == 'posix', 'process-group worker contract')
class HeadlessCancellationTests(unittest.TestCase):
    def worker(self, root):
        worker = root / 'worker.py'
        worker.write_text('''import json, os, subprocess, sys, time
from pathlib import Path
request = json.load(sys.stdin)
if request['url'].endswith('/slow'):
    Path(request['pid_file']).write_text(str(os.getpid()))
    child = subprocess.Popen([sys.executable, '-c',
        "import time; from pathlib import Path; time.sleep(2); Path(" + repr(request['marker']) + ").write_text('orphan')"], start_new_session=True)
    Path(request['pid_file'] + '.child').write_text(str(child.pid))
    print('browser still waiting', file=sys.stderr, flush=True)
    print(json.dumps({'content': 'late answer'}), flush=True)
    time.sleep(10)
else:
    print(json.dumps({'content': 'on-time answer'}), flush=True)
''')
        return worker

    def cleanup(self, pid_file):
        for owned in (pid_file, Path(str(pid_file) + '.child')):
            if owned.exists():
                try:
                    os.killpg(int(owned.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_timeout_stops_browser_children_retains_logs_and_runs_next_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = self.worker(root)
            pid_file, marker = root / 'pid', root / 'orphan'

            class LocalWorker(HeadlessProvider):
                def fetch(self, case, timeout):
                    # The outer budget includes preparation/launch and must
                    # cancel even when the inner worker deadline is later.
                    raw, stderr, stopped, code, _, _ = self._run_local_worker(
                        worker, {'url': case.url, 'pid_file': str(pid_file),
                                 'marker': str(marker)}, 10, 100_000)
                    return ProviderResult(not stopped,
                        '' if stopped else json.loads(raw)['content'],
                        error_type=stopped, metadata={'stderr': stderr, 'exit_code': code})

            provider = LocalWorker('local', {'type': 'headless_markdown', 'engine': 'obscura', 'binary': sys.executable})
            cases = [EvalCase(name, name, 'https://example.test/' + name, 'web', [])
                     for name in ('slow', 'fast')]
            try:
                with patch('socket.getaddrinfo', return_value=[(2,1,6,'',('93.184.216.34',443))]):
                    records, _ = run_evaluation(output_dir=root / 'run',
                    dataset_meta={'id': 'cancellation', 'cases': []},
                    dataset_path=root / 'dataset.json', cases=cases,
                    providers=[provider], provider_config_path=root / 'providers.json',
                    runs=1, timeout=.3, delay=0)
                self.assertEqual([row['case_id'] for row in records], ['slow', 'fast'])
                self.assertEqual(records[0]['error_type'], 'timeout')
                self.assertLess(records[0]['elapsed_ms'], 500)
                self.assertIn('browser still waiting', records[0]['provider_metadata']['stderr'])
                self.assertLess(records[0]['provider_metadata']['exit_code'], 0)
                self.assertEqual((root / 'run' / records[0]['artifact_path']).read_text(), '')
                self.assertEqual((root / 'run' / records[1]['artifact_path']).read_text(), 'on-time answer')
                time.sleep(2.1)
                self.assertFalse(marker.exists(), 'timed-out browser child continued physical work')
            finally:
                self.cleanup(pid_file)

    def test_cancellation_during_process_creation_kills_the_late_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = self.worker(root)
            pid_file, marker = root / 'pid', root / 'orphan'
            provider = HeadlessProvider('local', {'type': 'headless_markdown', 'engine': 'lightpanda'})
            created, release = threading.Event(), threading.Event()
            real_popen = subprocess.Popen
            children, results = [], []

            def slow_popen(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                if args and str(worker) in args[0]:
                    children.append(child)
                    created.set()
                    release.wait(1)
                return child

            provider.begin_call()
            def run():
                results.append(provider._run_local_worker(worker,
                    {'url': 'https://example.test/slow', 'pid_file': str(pid_file),
                     'marker': str(marker)}, 10, 100_000))
            thread = threading.Thread(target=run, daemon=True)
            try:
                with patch('harness.web_fetch.providers.headless.subprocess.Popen', side_effect=slow_popen):
                    thread.start()
                    self.assertTrue(created.wait(1))
                    provider.cancel()
                    release.set()
                    thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results[0][2], 'timeout')
                self.assertIsNotNone(children[0].poll())
            finally:
                release.set()
                for child in children:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                self.cleanup(pid_file)
                thread.join(1)

    @unittest.skipUnless(os.environ.get('CHROMIUM_BINARY'), 'set CHROMIUM_BINARY for real browser cancellation')
    def test_cancellation_stops_actual_chromium_detached_session(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        arrived = threading.Event()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                arrived.set()
                release.wait(10)
                body = b'<html><body>Completed body</body></html>'
                try:
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def process_rows():
            output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,pgid=,stat='], text=True)
            return {int(parts[0]): (int(parts[1]), int(parts[2]), parts[3])
                    for line in output.splitlines() if len(parts := line.split()) == 4}

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        provider = HeadlessProvider('chromium', {'engine': 'chromium',
            'binary': os.environ['CHROMIUM_BINARY'], 'allow_private_network': True})
        case = EvalCase('slow-navigation', 'slow-navigation',
            'http://127.0.0.1:' + str(server.server_port), 'web', [])
        results = []
        owned_groups = set()
        provider.begin_call()
        thread = threading.Thread(target=lambda: results.append(provider.fetch(case, 10)), daemon=True)
        try:
            thread.start()
            self.assertTrue(arrived.wait(8), 'real Chromium did not navigate to the controlled page')
            process = provider._active_process
            self.assertIsNotNone(process)
            rows = process_rows()
            owned = {process.pid}
            while (expanded := owned | {pid for pid, row in rows.items() if row[0] in owned}) != owned:
                owned = expanded
            owned_groups = {rows[pid][1] for pid in owned}
            self.assertTrue(owned_groups - {process.pid}, 'control did not exercise a detached browser session')
            provider.cancel()
            thread.join(2)
            self.assertFalse(thread.is_alive(), 'cancelled Chromium call did not finish')
            self.assertEqual(results[0].error_type, 'timeout')
            survivors = process_rows()
            self.assertFalse({pid for pid in owned if pid in survivors and not survivors[pid][2].startswith('Z')},
                             'physical Chromium work survived cancellation')
        finally:
            release.set()
            for group in owned_groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            thread.join(2)
            server.shutdown()
            server.server_close()
            server_thread.join(1)

    def test_cleanup_grace_requires_an_actively_cancellable_local_route(self):
        for config in ({'engine': 'kitesurf'}, {'engine': 'chromium', 'resource_sample_interval_ms': 50}):
            self.assertEqual(HeadlessProvider('remote', config).cancellation_cleanup_seconds, 0)
        self.assertGreater(HeadlessProvider('local', {'engine': 'chromium'}).cancellation_cleanup_seconds, 0)
