"""A threaded parent can collect concurrently using clean native workers."""

import json
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from harness.web_extract.example import create
from harness.web_extract.pipeline import load_run, probe
from tests.web_extract.test_self_hosted import RUNTIME


@pytest.mark.parametrize('concurrency', [1, 2])
def test_threaded_parent_collects_through_real_https_without_inheriting_locks(
    tmp_path, monkeypatch, concurrency
):
    home = create(tmp_path / 'example')
    certificate, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    config = tmp_path / 'tls.conf'
    config.write_text('''[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=localhost
[ext]
subjectAltName=DNS:localhost,IP:127.0.0.1
basicConstraints=critical,CA:TRUE
''', encoding='utf-8')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(certificate), '-days', '1',
                    '-config', str(config)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    barrier = threading.Barrier(2, timeout=10)
    lock = threading.Lock()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, raw):
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.respond((home / 'dataset/html' / self.path.rsplit('/', 1)[-1]).read_bytes())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with lock:
                requests.append(body)
                ordinal = len(requests)
            if concurrency > 1 and ordinal <= 2:
                barrier.wait()
            self.respond(b'{"success":true,"data":{"sku":"value"}}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('SSL_CERT_FILE', str(certificate))
    monkeypatch.setenv('EXTRACTION_API_KEY', 'synthetic-local-key')
    endpoint = f'https://localhost:{server.server_port}'
    target = home / 'runs/fresh-workers'
    parent_lock = threading.Lock()
    parent_lock.acquire()

    def inherited_call(*args, **kwargs):
        # A forked worker would inherit the locked parent and this stub.
        assert parent_lock.acquire(timeout=.1), 'Worker inherited parent state'
        raise AssertionError('Collection must use a clean interpreter')

    try:
        with patch('harness.web_extract.pipeline.load_runtime_env'), \
                patch('harness.web_extract.pipeline.validate_dataset'), \
                patch('harness.web_extract.snapshots.validate_dataset'), \
                patch('harness.web_extract.pipeline.provider_call', side_effect=inherited_call):
            probe(home, ['crawl4ai-json'], full=True, run_dir=target,
                  fixture_base=endpoint,
                  native_options={'endpoint': endpoint, 'runtime_identity': RUNTIME},
                  execution_policy={'purpose': 'quality', 'max_in_flight': concurrency,
                                    'serial_reason': 'One model slot',
                                    'backend': 'process',
                                    'resources': {'crawl4ai-json': {
                                        'capacity': concurrency, 'reason': 'Model service slots'}}})
    finally:
        parent_lock.release()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    _, rows = load_run(target)
    assert len(requests) == len(rows) == 3
    assert all(row['compatibility'] == 'structured_output' for row in rows)
    assert all(body['expected_runtime'] == RUNTIME for body in requests)
