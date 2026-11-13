from tests.web_search.fixtures import execution_profile
"""Collection scenarios: missing sources, empty billing, and retained failures."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize('selection', [['web-search'], ['--track', 'web-search']])
def test_documented_certified_entry_dispatches_web_search_with_certification(monkeypatch, selection):
    from harness.reporting.certified_cli import main

    calls = []
    monkeypatch.setattr('harness.web_search.runner.main', lambda args, **kwargs: calls.append((args, kwargs)))
    main([*selection, '--profile', 'frozen.json', '--output', 'run'])
    assert calls == [(['--profile', 'frozen.json', '--output', 'run'], {'certified': True})]


@pytest.fixture
def failed_postcheck(tmp_path, monkeypatch):
    from harness.web_search import runner

    profile = execution_profile()
    profile['methods'] = profile['methods'][1:2]
    profile['cases'] = profile['cases'][:1]
    profile['protocol'].update(reference_scope='identity', reference_retries=0, retry=False)
    profile['reference_checks'] = {url: dict(url=url, contains=['reference'])
                                   for case in profile['cases'] for url in case['anchors']}
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile))
    state = dict(references=0, provider_calls=0, post_status=0, post_exit=6, recover=True)

    def http(url, body=None, *args, **kwargs):
        receipt = dict(http_status=200, elapsed_ms=10, transport_exit=0, transport_error='')
        if body is not None:
            state['provider_calls'] += 1
            return receipt, b'{"results":[{"url":"https://example.org/page"}]}'
        state['references'] += 1
        if state['references'] == 2 or state['references'] > 2 and not state['recover']:
            return dict(receipt, http_status=state['post_status'], transport_exit=state['post_exit']), b''
        return receipt, b'reference ' * 20

    monkeypatch.setattr(runner, 'http_call', http)
    monkeypatch.setattr(runner, 'load_runtime_env', lambda: None)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture')
    return runner, path, state


def test_reference_finalization_preserves_complete_calls_and_original_failed_postcheck(tmp_path, failed_postcheck):
    from harness.web_search.report import load_run

    runner, profile, state = failed_postcheck
    source, output = tmp_path / 'source', tmp_path / 'finalized'
    runner.run(profile, source, certified=True)
    original = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    assert json.loads(original[Path('receipt.json')])['certified'] is False
    runner.main(['--profile', str(profile), '--output', str(output), '--finalize-references-from', str(source)], certified=True)
    assert state['provider_calls'] == 1 and state['references'] == 3
    assert all((source / path).read_bytes() == raw for path, raw in original.items())
    assert (output / 'results.jsonl').read_bytes() == original[Path('results.jsonl')]
    assert (output / 'reference-finalization/previous-after.json').read_bytes() == original[Path('references-after.json')]
    _, rows, receipt = load_run(output)
    assert len(rows) == 1 and receipt['certified'] is True
    assert receipt['ended_at'] == json.loads(original[Path('receipt.json')])['ended_at']
    raw = json.loads((output / 'reference-finalization/receipt.json').read_bytes())
    assert raw['provider_calls'] == 0 and len(raw['reference_urls']) == 1


@pytest.mark.parametrize('observed_status', [200, 404, 503])
def test_reference_finalization_rejects_observed_source_failures(tmp_path, failed_postcheck, observed_status):
    runner, profile, state = failed_postcheck
    state.update(post_status=observed_status, post_exit=0)
    source, output = tmp_path / 'source', tmp_path / 'finalized'
    runner.run(profile, source, certified=True)
    with pytest.raises(ValueError, match='unobserved post-check transport failures'):
        runner.finalize_references(profile, source, output)
    assert state['provider_calls'] == 1 and not output.exists()


def test_failed_reference_reobservation_cannot_certify_or_change_the_original(tmp_path, failed_postcheck):
    runner, profile, state = failed_postcheck
    source, output = tmp_path / 'source', tmp_path / 'finalized'
    runner.run(profile, source, certified=True)
    before = (source / 'receipt.json').read_bytes()
    state['recover'] = False
    with pytest.raises(ValueError, match='Post-check recovery failed'):
        runner.finalize_references(profile, source, output)
    assert (source / 'receipt.json').read_bytes() == before
    assert json.loads((output / 'receipt.json').read_bytes())['certified'] is False
    assert state['provider_calls'] == 1


@pytest.mark.parametrize('changed', ['provider_calls', 'reference_urls', 'ended_at'])
def test_reference_finalization_replays_its_zero_call_and_complete_scope_contract(tmp_path, failed_postcheck, changed):
    from harness.record_io import bytes_sha256
    from harness.web_search.report import load_run

    runner, profile, state = failed_postcheck
    source, output = tmp_path / 'source', tmp_path / 'finalized'
    runner.run(profile, source, certified=True)
    runner.finalize_references(profile, source, output)
    receipt = json.loads((output / 'receipt.json').read_bytes())
    if changed == 'ended_at':
        receipt['ended_at'] = 'Changed collection date'
    else:
        path = output / receipt['reference_finalization']['path']
        proof = json.loads(path.read_bytes())
        proof[changed] = 1 if changed == 'provider_calls' else []
        path.write_text(json.dumps(proof))
        receipt['reference_finalization']['sha256'] = bytes_sha256(path.read_bytes())
        for binding in receipt['bindings']:
            if binding['path'] == path.relative_to(output).as_posix():
                binding['sha256'] = bytes_sha256(path.read_bytes())
    (output / 'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='finalization|complete collection'):
        load_run(output)
    assert state['provider_calls'] == 1


@pytest.mark.parametrize('retry', [None, False, True])
@pytest.mark.parametrize('failure', ['transport', 'http', 'invalid', 'empty'])
def test_retries_require_explicit_opt_in(tmp_path, retry, failure):
    from harness.web_search.runner import run
    from harness.web_search.report import load_run

    profile = execution_profile()
    profile['methods'] = profile['methods'][1:2]
    profile['cases'] = profile['cases'][:1]
    if retry is None:
        profile['protocol'].pop('retry')
    else:
        profile['protocol']['retry'] = retry
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile), encoding='utf-8')
    calls = []

    def http(url, body=None, *args, **kwargs):
        receipt = dict(http_status=200, elapsed_ms=10, transport_exit=0, transport_error='')
        if body is None:
            return receipt, b'reference ' * 20
        calls.append(body)
        if len(calls) == 1:
            if failure == 'transport':
                return dict(receipt, http_status=0, transport_exit=28, transport_error='Timed out'), b''
            if failure == 'http':
                return dict(receipt, http_status=503), b'{"error":"busy"}'
            if failure == 'invalid':
                return receipt, b'not JSON'
            return receipt, b'{"results":[]}'
        return receipt, b'{"results":[{"url":"https://example.org/page"}]}'

    with (patch('harness.web_search.runner.http_call', side_effect=http),
          patch('harness.web_search.runner.load_runtime_env'),
          patch.dict(os.environ, DEEPSEEK_API_KEY='fixture')):
        run(path, tmp_path/'run', certified=True)
    _, rows, receipt = load_run(tmp_path/'run')
    expected = 2 if retry is True and failure != 'empty' else 1
    assert len(calls) == expected
    assert len(rows) == 1
    assert len(rows[0]['attempts']) == expected
    assert bool(rows[0]['error']) == (failure != 'empty' and expected == 1)
    assert receipt['certified'] is True


@pytest.mark.parametrize('retry', ['false', 'true', '', 0, 1, 2, None, [], {}])
def test_invalid_retry_settings_are_rejected_before_collection(tmp_path, retry):
    from harness.web_search.runner import run

    profile = execution_profile()
    profile['protocol']['retry'] = retry
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(profile), encoding='utf-8')
    with (patch('harness.web_search.runner.http_call') as call,
          patch('harness.web_search.runner.load_runtime_env'),
          pytest.raises(ValueError, match='Retry must be a boolean')):
        run(path, tmp_path/'run', certified=True)
    call.assert_not_called()
    assert not (tmp_path/'run').exists()


def test_transport_does_not_inherit_retries_from_user_curl_configuration(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from harness.web_search.runner import http_call

    requests = []

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            requests.append(self.path)
            self.send_response(503 if len(requests) == 1 else 200)
            self.end_headers()
            self.wfile.write(b'busy' if len(requests) == 1 else b'retried')

        def log_message(self, *args):
            pass

    (tmp_path/'.curlrc').write_text('retry = 2\n', encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with patch.dict(os.environ, CURL_HOME=str(tmp_path), NO_PROXY='127.0.0.1'):
            receipt, raw = http_call(f'http://127.0.0.1:{server.server_port}/search', {'query':'fixture'}, timeout=3)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    assert requests == ['/search']
    assert receipt['http_status'] == 503
    assert raw == b'busy'


def test_terminal_interrupt_retains_a_completed_curl_response(tmp_path):
    import json
    import os
    import signal
    import subprocess
    import sys
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    ready, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            ready.set()
            assert release.wait(5)
            raw = b'{"answer":"retained"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    saved = tmp_path / "response.json"
    code = '''import json,sys
from pathlib import Path
from harness.benchmark_execution import _retain_call_ownership
from harness.web_search.runner import http_call
try:
    with _retain_call_ownership():
        metadata, raw = http_call(sys.argv[1], timeout=5)
        Path(sys.argv[2]).write_text(json.dumps([metadata, raw.decode()]), encoding="utf-8")
except KeyboardInterrupt:
    pass
'''
    process = subprocess.Popen(
        [sys.executable, "-c", code, f"http://127.0.0.1:{server.server_port}/", str(saved)],
        start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert ready.wait(5)
        os.killpg(process.pid, signal.SIGINT)
        time.sleep(.05)
        release.set()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        metadata, raw = json.loads(saved.read_text(encoding="utf-8"))
        assert metadata["http_status"] == 200 and json.loads(raw) == {"answer": "retained"}
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

from harness.record_io import bytes_sha256
from harness.web_search.report import load_run
from harness.web_search.runner import run


def test_reference_transport_retry_retains_failed_bytes_and_does_not_retry_wrong_identity(tmp_path):
    from harness.web_search.runner import capture_references

    url = 'https://reference.test/document'
    profile = {'cases': [{'anchors': [url]}], 'protocol': {'reference_retries': 2},
               'reference_checks': {url: {'contains': ['Independent identity']}}}
    okay = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
    failed = dict(okay, http_status=0, transport_exit=35, transport_error='TLS interrupted')
    with patch('harness.web_search.runner.http_call', side_effect=[(failed, b'partial'), (okay, b'Independent identity ' * 10)]) as calls, patch('harness.web_search.runner.time.sleep'):
        refs = capture_references(profile, tmp_path, 'before')
    assert refs[url]['available'] and calls.call_count == 2
    assert len(refs[url]['attempts']) == 2
    assert (tmp_path / refs[url]['attempts'][0]['path']).read_bytes() == b'partial'
    with patch('harness.web_search.runner.http_call', return_value=(okay, b'Wrong page ' * 30)) as calls:
        refs = capture_references(profile, tmp_path, 'after')
    assert not refs[url]['available'] and calls.call_count == 1


def test_reference_transport_retry_exhaustion_fails_closed(tmp_path):
    from harness.web_search.runner import capture_references

    url = 'https://reference.test/document'
    profile = {'cases': [{'anchors': [url]}], 'protocol': {'reference_retries': 2}}
    failed = dict(http_status=0, elapsed_ms=1, transport_exit=35, transport_error='TLS interrupted')
    with patch('harness.web_search.runner.http_call', return_value=(failed, b'')) as calls, patch('harness.web_search.runner.time.sleep'):
        refs = capture_references(profile, tmp_path, 'before')
    assert not refs[url]['available'] and calls.call_count == 3
    assert len(refs[url]['attempts']) == 3


class CollectionScenarios(unittest.TestCase):
    def test_ctrl_c_drains_active_calls_and_stops_later_cases(self):
        import signal
        import threading

        from harness import benchmark_execution

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.profile(root)
            profile = json.loads(source.read_text(encoding="utf-8"))
            profile['cases'].append(dict(profile['cases'][0], id='second-case', query='Second'))
            methods = profile['methods']
            profile['protocol'].update(concurrency=len(methods), order='bounded calls',
                                       retry=False, interval_s=0)
            profile['execution_policy'] = {
                'purpose': 'quality', 'max_in_flight': len(methods),
                'resources': {m['id']: {'capacity': 1, 'reason': 'Ordered calls'} for m in methods}}
            source.write_text(json.dumps(profile), encoding="utf-8")
            ready = threading.Barrier(len(methods) + 1, timeout=3)
            release = threading.Event()
            calls = []
            original_wait = benchmark_execution.wait
            interrupted = False

            def http(url, body=None, header=None, timeout=20):
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
                if body is None:
                    return receipt, b'reference ' * 20
                calls.append(body)
                if len(calls) <= len(methods):
                    ready.wait()
                    assert release.wait(timeout=3)
                return receipt, b'{"results":[]}'

            def wait_once(futures, **kwargs):
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    ready.wait()
                    try:
                        signal.raise_signal(signal.SIGINT)
                    finally:
                        release.set()
                return original_wait(futures, **kwargs)

            with (
                patch('harness.web_search.runner.http_call', side_effect=http),
                patch('harness.web_search.runner.load_runtime_env'),
                patch.object(benchmark_execution, 'wait', side_effect=wait_once),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY='test',
                           DEEPSEEK_API_KEY='test', BOCHA_API_KEY='test'),
                self.assertRaises(KeyboardInterrupt),
            ):
                run(source, root / 'run', certified=True)
            self.assertEqual(len(calls), len(methods))
            rows = (root / 'run/results.jsonl').read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), len(methods))

    def test_stop_while_waiting_for_reservation_does_not_send_another_request(self):
        import threading
        from types import SimpleNamespace
        from harness.web_search import runner

        blocked, release = threading.Event(), threading.Event()
        calls = []
        owner = None
        real_lock = threading.Lock()

        class AdmissionLock:
            def __enter__(self):
                nonlocal owner
                current = threading.get_ident()
                if owner is None:
                    owner = current
                elif current != owner and not release.is_set():
                    blocked.set()
                    assert release.wait(3)
                real_lock.acquire()

            def __exit__(self, *args):
                real_lock.release()

        class StopEvent(threading.Event):
            def set(self):
                super().set()
                release.set()

        def http(url, body=None, header=None, timeout=20):
            receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
            if body is None:
                return receipt, b'reference ' * 20
            calls.append(body)
            assert blocked.wait(3)
            return dict(receipt, http_status=401), b'{"error":"invalid account"}'

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.profile(root)
            profile = json.loads(source.read_text(encoding='utf-8'))
            profile['methods'] = profile['methods'][:2]
            profile['protocol'].update(concurrency=2, order='bounded calls', retry=False)
            profile['execution_policy'] = {
                'purpose': 'quality', 'max_in_flight': 2,
                'resources': {m['id']: {'capacity': 1, 'reason': 'One call per provider'}
                              for m in profile['methods']}}
            source.write_text(json.dumps(profile), encoding='utf-8')
            with patch.object(runner, 'threading', SimpleNamespace(Event=StopEvent, Lock=AdmissionLock)), \
                    patch.object(runner, 'http_call', side_effect=http), \
                    patch.object(runner, 'load_runtime_env'), \
                    patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY='test', DEEPSEEK_API_KEY='test'):
                with self.assertRaisesRegex(ValueError, 'account error'):
                    run(source, root / 'run', certified=True)
            rows = (root / 'run/results.jsonl').read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(list((root / 'run/calls').glob('*.json'))), 1)

    def test_shared_account_variants_require_an_explicit_account_capacity(self):
        from harness.web_search.core import validate_profile

        profile = execution_profile()
        profile['protocol'].update(concurrency=2, order='bounded calls', interval_s=0)
        profile['methods'][1]['key_env'] = profile['methods'][0]['key_env']
        profile['execution_policy'] = {
            'purpose': 'quality', 'max_in_flight': 2,
            'resources': {m['id']: {'capacity': 1, 'reason': 'Ordered provider calls'}
                          for m in profile['methods']},
        }
        with self.assertRaisesRegex(ValueError, 'shared account'):
            validate_profile(profile)
        account = 'account/' + profile['methods'][0]['key_env']
        profile['execution_policy']['resources'][account] = {
            'capacity': 1, 'reason': 'Both variants consume the same account quota'}
        validate_profile(profile)

    def test_independent_queries_of_one_stateless_provider_can_overlap(self):
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.profile(root)
            profile = json.loads(source.read_text(encoding='utf-8'))
            profile['methods'] = profile['methods'][:1]
            profile['cases'].append(dict(profile['cases'][0], id='second-case', query='Second query'))
            method = profile['methods'][0]['id']
            profile['protocol'].update(concurrency=2, order='bounded calls', retry=False)
            profile['execution_policy'] = {
                'purpose': 'quality', 'max_in_flight': 2,
                'resources': {method: {'capacity': 2}},
            }
            source.write_text(json.dumps(profile), encoding='utf-8')
            barrier = threading.Barrier(2, timeout=3)
            calls = []

            def http(url, body=None, header=None, timeout=20):
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
                if body is None:
                    return receipt, b'reference ' * 20
                calls.append(body)
                barrier.wait()
                return receipt, b'{"results":[]}'

            with patch('harness.web_search.runner.http_call', side_effect=http), \
                 patch('harness.web_search.runner.load_runtime_env'), \
                 patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY='test'):
                run(source, root / 'run', certified=True)
            _, rows, receipt = load_run(root / 'run')
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(calls), 2)
            self.assertTrue(receipt['certified'])

    def test_bounded_calls_overlap_with_complete_checkpointed_results(self):
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.profile(root)
            profile = json.loads(source.read_text(encoding='utf-8'))
            methods = profile['methods']
            profile['protocol'].update(concurrency=len(methods), order='bounded calls', retry=False)
            profile['execution_policy'] = {
                'purpose': 'quality', 'max_in_flight': len(methods),
                'resources': {m['id']: {'capacity': 1, 'reason': 'Per-account pacing'} for m in methods},
            }
            source.write_text(json.dumps(profile), encoding='utf-8')
            barrier = threading.Barrier(len(methods), timeout=3)
            calls = []
            lock = threading.Lock()

            def http(url, body=None, header=None, timeout=20):
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
                if body is None:
                    return receipt, b'reference ' * 20
                with lock:
                    calls.append(url)
                barrier.wait()
                return receipt, b'{"results":[]}'

            with patch('harness.web_search.runner.http_call', side_effect=http), \
                 patch('harness.web_search.runner.load_runtime_env'), \
                 patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY='test', DEEPSEEK_API_KEY='test', BOCHA_API_KEY='test'):
                run(source, root / 'run', certified=True)
                _, rows, receipt = load_run(root / 'run')
                self.assertEqual(len(rows), len(methods))
                self.assertEqual(len(calls), len(methods))
                self.assertTrue(receipt['certified'])
                saved = (root / 'run/results.jsonl').read_bytes()
                with self.assertRaisesRegex(ValueError, 'completed collection'):
                    run(source, root / 'run', certified=True, resume=True)
                self.assertEqual((root / 'run/results.jsonl').read_bytes(), saved)
                self.assertEqual(len(calls), len(methods))

    def test_provider_batches_finish_all_cases_and_rounds_without_repeating_saved_calls(self):
        for explicit in (False, True):
            with self.subTest(explicit_order=explicit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                p = self.profile(root)
                profile = json.loads(p.read_text(encoding="utf-8"))
                profile["cases"].append(
                    dict(profile["cases"][0], id="second-case", query="Second query")
                )
                profile["protocol"].update(rounds=2, retry=False)
                profile["protocol"].pop("order", None)
                if explicit:
                    profile["protocol"]["order"] = "complete provider batches"
                p.write_text(json.dumps(profile), encoding="utf-8")
                calls = []
                interrupted = False

                def http(url, body=None, header=None, timeout=20, calls=calls):
                    nonlocal interrupted
                    receipt = dict(
                        http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""
                    )
                    if body is None:
                        return receipt, b"reference " * 20
                    if len(calls) == 3 and not interrupted:
                        interrupted = True
                        raise KeyboardInterrupt
                    calls.append((url, body.get("query")))
                    return receipt, b'{"results":[]}'

                with (
                    patch("harness.web_search.runner.http_call", side_effect=http),
                    patch("harness.web_search.runner.load_runtime_env"),
                    patch.dict(
                        os.environ, LEXMOUNT_SEARCH_API_KEY="test",
                        DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test",
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        run(p, root / "run", certified=True)
                    saved = (root / "run/results.jsonl").read_bytes()
                    run(p, root / "run", resume=True, certified=True)
                _, rows, receipt = load_run(root / "run")
                expected = [
                    (method["id"], case["id"], round_id)
                    for method in profile["methods"] for round_id in (0, 1)
                    for case in profile["cases"]
                ]
                self.assertEqual([(r["method"], r["case_id"], r["round"]) for r in rows], expected)
                self.assertEqual(len(calls), len(expected))
                self.assertTrue((root / "run/results.jsonl").read_bytes().startswith(saved))
                self.assertTrue(receipt["certified"])
                contract = json.loads((root / "run/run-contract.json").read_text(encoding="utf-8"))
                self.assertNotIn("harness/web_search/report.py", contract["implementation"])
                self.assertTrue({"uv.lock", "pyproject.toml", "harness/web_search/core.py", "harness/distribution.py"}.issubset(contract["implementation"]))
                bound = {item["path"]: item["sha256"] for item in receipt["bindings"]}
                for name, identity in contract["implementation"].items():
                    self.assertEqual(bound["implementation/" + name], identity)
                (root / "run/implementation/uv.lock").write_bytes(b"changed runtime lock")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    load_run(root / "run")

    def test_unknown_execution_order_fails_before_network_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text(encoding="utf-8"))
            profile["protocol"]["order"] = "provider batch typo"
            p.write_text(json.dumps(profile), encoding="utf-8")
            with patch("harness.web_search.runner.http_call") as http:
                with self.assertRaisesRegex(ValueError, "execution order"):
                    run(p, root / "run", certified=True)
                http.assert_not_called()
            self.assertFalse((root / "run").exists())

    def test_resume_retains_complete_cases_and_recollects_whole_interrupted_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text(encoding="utf-8"))
            profile["cases"].append(dict(profile["cases"][0], id="second-case"))
            profile["protocol"]["retry"] = True
            profile["protocol"]["order"] = "rotate methods by case and round"
            p.write_text(json.dumps(profile), encoding="utf-8")
            calls = []
            recovered = False

            def http(url, body=None, header=None, timeout=20):
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                if body is None:
                    return receipt, b"reference " * 20
                calls.append(url)
                if not recovered and len(calls) > 2:
                    return dict(receipt, http_status=402), b'{"error":"Insufficient Balance"}'
                return receipt, b'{"results":[{"url":"https://example.org/a","title":"Fixture"}]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                with self.assertRaisesRegex(ValueError, "Provider account error"):
                    run(p, root / "run", certified=True)
                original = (root / "run/results.jsonl").read_bytes()
                # A process can stop partway through the final row write.
                with (root / "run/results.jsonl").open("ab") as stream:
                    stream.write(b'{"id":"incomplete')
                recovered = True
                run(p, root / "run", resume=True, certified=True)
            _, rows, receipt = load_run(root / "run")
            self.assertEqual(len(calls), 10)  # four interrupted calls, then both three-method cases
            self.assertEqual(len(rows), 6)
            self.assertTrue(receipt["certified"])
            self.assertEqual((root / "run/resume-1/previous-results.jsonl").read_bytes(),
                             original + b'{"id":"incomplete')
            self.assertEqual(len(list((root / "run/resume-1/calls").glob("*.json"))), 4)
            contract = json.loads((root / "run/resume-1/contract.json").read_text(encoding="utf-8"))
            self.assertEqual(
                contract["recollect_case_ids"], ["fixture-0", "second-case"]
            )
            archived = next((root / "run/resume-1/calls").glob("*.json"))
            archived.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_account_errors_stop_the_cohort_after_declared_retry(self):
        for status in (401, 402):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                p = self.profile(root)
                profile = json.loads(p.read_text(encoding="utf-8"))
                profile["protocol"]["retry"] = True
                p.write_text(json.dumps(profile), encoding="utf-8")
                calls = []

                def http(url, body=None, header=None, timeout=20, calls=calls, status=status):
                    receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                    if body is None:
                        return receipt, b"reference " * 20
                    calls.append(url)
                    return dict(receipt, http_status=status), b'{"error":{"message":"account unavailable"}}'

                with (
                    patch("harness.web_search.runner.http_call", side_effect=http),
                    patch("harness.web_search.runner.load_runtime_env"),
                    patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
                ):
                    with self.assertRaisesRegex(ValueError, "Provider account error"):
                        run(p, root / "run", certified=True)
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(set(calls)), 1)  # no spending on the next participant
                rows = (root / "run/results.jsonl").read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(rows), 1)
                self.assertEqual(len(json.loads(rows[0])["attempts"]), 2)
                self.assertTrue((root / "run/interruption.json").exists())
                self.assertFalse((root / "run/receipt.json").exists())

    def profile(self, root):
        d = execution_profile()
        d["cases"] = d["cases"][:1]
        d["protocol"].update(rounds=1, interval_s=0)
        d["protocol"].pop("reference_scope", None)
        d.pop("reference_checks", None)
        p = root / "input.json"
        p.write_text(json.dumps(d), encoding="utf-8")
        return p

    def test_published_label_snapshot_requires_the_frozen_hash(self):
        from harness.web_search.runner import reference_available
        from harness.record_io import bytes_sha256
        url = "https://example.org/pinned-labels.csv"
        raw = b"query,answer,url\n" * 20
        profile = {"reference_artifacts": {url: {"sha256": bytes_sha256(raw)}}}
        receipt = {"http_status": 200, "transport_exit": 0}
        self.assertTrue(reference_available(profile, url, receipt, raw))
        self.assertFalse(reference_available(profile, url, receipt, raw + b"changed"))
        self.assertFalse(reference_available(profile, url, dict(receipt, transport_exit=28), raw))

    def test_failure_is_retained_after_the_single_declared_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            order = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(
                        http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""
                    ), b"reference " * 20
                order.append(url)
                return dict(
                    http_status=503 if url == json.loads(p.read_text(encoding="utf-8"))["methods"][0]["endpoint"] else 200,
                    elapsed_ms=20,
                    transport_exit=0,
                    transport_error="",
                ), (b'{"error":"Unavailable"}' if url == json.loads(p.read_text(encoding="utf-8"))["methods"][0]["endpoint"] else b'{"results":[]}')

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                run(p, root / "run", certified=True)
            _, rows, receipt = load_run(root / "run")
            self.assertEqual(len(order), 4)
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(rows[0]["attempts"]), 2)
            self.assertEqual(rows[1]["cost"], 0)
            self.assertTrue(rows[0]["error"])
            self.assertTrue(receipt["certified"])
            ref = next((root / "run/references").glob("*.bin"))
            ref.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_failed_reference_blocks_calls_before_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            with (
                patch(
                    "harness.web_search.runner.http_call",
                    return_value=(
                        dict(http_status=404, elapsed_ms=1, transport_exit=0, transport_error=""),
                        b"absent",
                    ),
                ) as call,
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                with self.assertRaises(ValueError):
                    run(p, root / "run", certified=True)
            self.assertEqual(call.call_count, 1)

    def test_failed_call_retries_once_and_only_final_response_is_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text(encoding="utf-8"))
            profile["methods"] = [profile["methods"][1]]
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile), encoding="utf-8")
            calls = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), b"reference " * 20
                calls.append(url)
                if len(calls) == 1:
                    return dict(http_status=503, elapsed_ms=20, transport_exit=0, transport_error=""), b'{"error":"busy"}'
                return dict(http_status=200, elapsed_ms=30, transport_exit=0, transport_error=""), b'{"results":[{"url":"https://example.org/p"}]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, DEEPSEEK_API_KEY="test"),
            ):
                run(p, root / "run", certified=True)
            _, rows, _ = load_run(root / "run")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]["attempts"]), 2)
            self.assertEqual(rows[0]["results"][0]["url"], "https://example.org/p")
            self.assertGreaterEqual(rows[0]["elapsed_ms"], 50)
            first = root / "run" / rows[0]["attempts"][0]["evidence"]["path"]
            first.write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_valid_empty_response_is_a_result_not_an_api_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text(encoding="utf-8"))
            profile["methods"] = [profile["methods"][1]]
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile), encoding="utf-8")
            calls = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), b"reference " * 20
                calls.append(url)
                return dict(http_status=200, elapsed_ms=20, transport_exit=0, transport_error=""), b'{"results":[]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, DEEPSEEK_API_KEY="test"),
            ):
                run(p, root / "run", certified=True)
            _, rows, _ = load_run(root / "run")
            self.assertEqual(len(calls), 1)
            self.assertEqual(rows[0]["results"], [])


if __name__ == "__main__":
    unittest.main()


class ReferenceCaptureScenarios(unittest.TestCase):
    def test_identity_check_rejects_an_unrelated_http_200_page(self):
        from harness.web_search.runner import capture_references

        profile = {
            "protocol": {"reference_scope": "identity"},
            "cases": [{"anchors": ["https://example.org/paper"]}],
            "reference_checks": {
                "https://example.org/paper": {
                    "url": "https://example.org/paper",
                    "contains": ["Distinct paper title"],
                }
            },
        }
        receipt = dict(http_status=200, transport_exit=0)
        for raw, expected in [
            (b"login screen " * 30, False),
            (b"Distinct paper title " * 30, True),
        ]:
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch("harness.web_search.runner.http_call", return_value=(receipt, raw)),
            ):
                refs = capture_references(profile, Path(tmp), "before")
                self.assertEqual(refs["https://example.org/paper"]["available"], expected)

    def test_page_chrome_changes_do_not_change_verified_identity(self):
        from harness.web_search.runner import reference_changes

        before = {"u": {"available": True, "sha256": "a"}}
        after = {"u": {"available": True, "sha256": "b"}}
        self.assertEqual(
            reference_changes({"protocol": {"reference_scope": "identity"}}, before, after), []
        )
        self.assertEqual(reference_changes({"protocol": {}}, before, after), ["u"])
        after["u"]["available"] = False
        self.assertEqual(
            reference_changes({"protocol": {"reference_scope": "identity"}}, before, after), ["u"]
        )

    def test_http_200_with_incomplete_transfer_is_not_qualified(self):
        from harness.web_search.runner import capture_references

        profile = {"protocol": {}, "cases": [{"anchors": ["https://example.org/"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dict(
                http_status=200, elapsed_ms=20000, transport_exit=28, transport_error="timeout"
            )
            with patch(
                "harness.web_search.runner.http_call", return_value=(receipt, b"partial " * 50)
            ):
                refs = capture_references(profile, Path(tmp), "before")
            self.assertFalse(refs["https://example.org/"]["available"])


class TransportAndResumeBoundaries(unittest.TestCase):
    def test_subprocess_timeout_is_a_transport_result_with_partial_bytes(self):
        import subprocess
        from harness.web_search.runner import http_call
        def timeout(command, **kwargs):
            Path(command[command.index('--output') + 1]).write_bytes(b'partial\r\n')
            raise subprocess.TimeoutExpired(command, 25)
        with patch('harness.web_search.runner.subprocess.run', side_effect=timeout):
            receipt, raw = http_call('https://provider.test', {})
        self.assertEqual(receipt['transport_exit'], 28)
        self.assertEqual(receipt['http_status'], 0)
        self.assertEqual(raw, b'partial\r\n')

    def test_resume_rejects_live_source_drift_before_archiving_results(self):
        from harness.web_search.runner import prepare_resume
        from harness.record_io import bytes_sha256
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = b'{"cases":[],"methods":[],"protocol":{"rounds":1}}'
            path = root / 'input.json'; path.write_bytes(profile)
            (root/'profile.json').write_bytes(profile)
            (root/'run-contract.json').write_text(json.dumps({'profile_sha256':bytes_sha256(profile),'implementation':{}}), encoding="utf-8")
            (root/'results.jsonl').write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'same live'):
                prepare_resume(path,root)
            self.assertFalse(list(root.glob('resume-*')))

    def test_complete_bad_jsonl_line_is_rejected_but_interrupted_tail_is_accepted(self):
        from harness.record_io import read_completed_records
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'rows.jsonl'
            path.write_bytes(b'{"id":"complete"}\n{"partial"')
            self.assertEqual(read_completed_records(path), [{'id':'complete'}])
            path.write_bytes(b'{"id":"complete"}\n{"partial"\n')
            with self.assertRaises(ValueError):
                read_completed_records(path)


def test_subprocess_timeout_retries_and_finishes_the_complete_matrix():
    from types import SimpleNamespace
    import subprocess
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        profile_path = CollectionScenarios().profile(root)
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile['methods'] = [profile['methods'][1]]
        profile['protocol']['retry'] = True
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        provider_calls = []
        def subprocess_call(command, **kwargs):
            output = Path(command[command.index('--output') + 1])
            if '--data-binary' not in command:
                output.write_bytes(b'reference ' * 20)
            else:
                provider_calls.append(command)
                if len(provider_calls) == 1:
                    output.write_bytes(b'partial upstream response')
                    raise subprocess.TimeoutExpired(command, 25)
                output.write_bytes(b'{"results":[{"url":"https://example.org/success"}]}')
            return SimpleNamespace(stdout='200 0.001 0.002 0 0 0 200', stderr='', returncode=0)
        with (patch('harness.web_search.runner.subprocess.run',side_effect=subprocess_call),
              patch('harness.web_search.runner.load_runtime_env'),
              patch.dict(os.environ, DEEPSEEK_API_KEY='fixture')):
            run(profile_path,root/'run', certified=True)
        _, rows, receipt = load_run(root/'run')
        assert len(rows) == 1 and receipt['certified']
        assert len(provider_calls) == 2
        assert rows[0]['attempts'][0]['transport_exit'] == 28
        assert rows[0]['attempts'][1]['transport_exit'] == 0
        assert rows[0]['results'][0]['url'] == 'https://example.org/success'
        assert (root/'run'/rows[0]['attempts'][0]['evidence']['path']).read_bytes() == b'partial upstream response'


def test_direct_diagnostic_collection_remains_uncertified(tmp_path):
    profile_path = CollectionScenarios().profile(tmp_path)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile['methods'] = [profile['methods'][1]]
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    calls = []
    def http(url, body=None, **kwargs):
        calls.append(body is not None)
        return {'http_status':200,'elapsed_ms':1,'transport_exit':0,'transport_error':''}, (
            b'reference '*20 if body is None else b'{"results":[]}'
        )
    with (patch('harness.web_search.runner.http_call',side_effect=lambda url,body=None,*args,**kwargs:http(url,body)),
          patch('harness.web_search.runner.load_runtime_env'),
          patch.dict(os.environ,DEEPSEEK_API_KEY='fixture')):
        from harness.web_search.runner import main as direct_main
        direct_main(['--profile',str(profile_path),'--output',str(tmp_path/'run'),'--diagnostic'])
    receipt = json.loads((tmp_path/'run/receipt.json').read_text(encoding="utf-8"))
    assert receipt['complete'] is True
    assert receipt['certified'] is False
    assert json.loads((tmp_path/'run/run-contract.json').read_text(encoding="utf-8"))['diagnostic'] is True
    assert calls.count(True) == 1


def test_certified_entry_rejects_unqualified_sources_before_provider_calls(tmp_path):
    from harness.web_fetch.certification import main as certified_main
    profile_path = CollectionScenarios().profile(tmp_path)
    calls=[]
    def http(url,body=None,*args,**kwargs):
        calls.append(body is not None)
        return {'http_status':404,'elapsed_ms':1,'transport_exit':0,'transport_error':''}, b'not found'
    import pytest
    with (patch('harness.web_search.runner.http_call',side_effect=http),
          patch('harness.web_search.runner.load_runtime_env'),
          patch.dict(os.environ,LEXMOUNT_SEARCH_API_KEY='fixture',DEEPSEEK_API_KEY='fixture',BOCHA_API_KEY='fixture')):
        with pytest.raises(SystemExit) as failure:
            certified_main(['web-search','--profile',str(profile_path),'--output',str(tmp_path/'run')])
    assert failure.value.code == 2
    assert not any(calls)
    assert not (tmp_path/'run/receipt.json').exists()


def test_resume_reference_drift_preserves_every_saved_result_and_call(tmp_path):
    import pytest
    profile = CollectionScenarios().profile(tmp_path)
    output = tmp_path / "run"
    reference = b"reference " * 20
    def http(url, body=None, *args, **kwargs):
        if body is None:
            return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), reference
        return dict(http_status=402, elapsed_ms=1, transport_exit=0, transport_error=""), b'{"error":"account"}'
    with patch("harness.web_search.runner.http_call", side_effect=http), patch("harness.web_search.runner.load_runtime_env"), patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="fixture", DEEPSEEK_API_KEY="fixture", BOCHA_API_KEY="fixture"):
        with pytest.raises(ValueError, match="Provider account error"):
            run(profile, output, certified=True)
        before = {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}
        reference = b"changed source " * 20
        with pytest.raises(ValueError, match="Reference recheck failed"):
            run(profile, output, resume=True, certified=True)
        assert not list(output.glob("resume-*"))
        for name, raw in before.items():
            assert (output / name).read_bytes() == raw


@pytest.mark.parametrize("checkpoint_state", ["partial", "legacy", "stale-final"])
def test_interrupted_retry_resumes_only_the_missing_physical_attempt(tmp_path, checkpoint_state):
    import signal
    import pytest
    from harness.web_search import runner

    profile = execution_profile()
    profile['methods'] = profile['methods'][:1]
    profile['cases'] = profile['cases'][:1]
    method = profile['methods'][0]
    profile['protocol'].update(order='bounded calls', rounds=1, concurrency=1, retry=True,
                               interval_s=0, max_calls_per_method=2)
    profile['execution_policy'] = {
        'purpose': 'quality', 'max_in_flight': 1, 'serial_reason': 'One provider account',
        'resources': {method['id']: {'capacity': 1, 'reason': 'One provider account'}}}
    source, output = tmp_path / 'profile.json', tmp_path / 'run'
    source.write_text(json.dumps(profile), encoding="utf-8")
    sends = []

    def http(url, body=None, header=None, timeout=20):
        receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
        if body is None:
            return receipt, b'reference ' * 20
        sends.append(body)
        if len(sends) == 1:
            signal.raise_signal(signal.SIGINT)
            return dict(receipt, http_status=503), b'{"error":"temporary"}'
        return receipt, b'{"results":[]}'

    with (patch.object(runner, 'http_call', side_effect=http),
          patch.object(runner, 'load_runtime_env'),
          patch.dict(os.environ, {method['key_env']: 'fixture'})):
        with pytest.raises(KeyboardInterrupt):
            runner.run(source, output, certified=True)
        assert len(sends) == 1
        assert not (output / 'results.jsonl').read_bytes()
        saved = next((output / 'partial-results').glob('*.json'))
        partial = json.loads(saved.read_text(encoding="utf-8"))['row']
        first = output / partial['attempts'][0]['evidence']['path']
        original = first.read_bytes()
        if checkpoint_state == 'legacy':
            (output / 'results.jsonl').write_text(json.dumps(partial) + '\n', encoding='utf-8')
            saved.unlink()
        if checkpoint_state == 'stale-final':
            unlink = Path.unlink
            def interrupt_cleanup(path, *args, **kwargs):
                if path == saved and len(sends) == 2:
                    raise KeyboardInterrupt
                return unlink(path, *args, **kwargs)
            with patch.object(Path, 'unlink', interrupt_cleanup), pytest.raises(KeyboardInterrupt):
                runner.run(source, output, certified=True, resume=True)
            assert len(sends) == 2 and saved.exists()
        runner.run(source, output, certified=True, resume=True)
    rows = [json.loads(line) for line in
            (output / 'results.jsonl').read_text(encoding="utf-8").splitlines()]
    assert len(sends) == 2 and len(rows) == 1
    assert [a['http_status'] for a in rows[0]['attempts']] == [503, 200]
    assert first.read_bytes() == original
    assert not list((output / 'partial-results').glob('*.json'))


def test_certified_entry_rejects_diagnostic_before_any_call(tmp_path, monkeypatch):
    import pytest
    from harness.web_search import runner
    monkeypatch.setattr(runner, "http_call", lambda *a, **kw: pytest.fail("Network called"))
    with pytest.raises(SystemExit) as error:
        runner.main(["--profile", "missing.json", "--output", str(tmp_path / "run"), "--diagnostic"], certified=True)
    assert error.value.code == 2


def test_completed_resume_rejected_without_network_or_saved_file_changes(tmp_path, monkeypatch):
    import pytest
    from harness.web_search import runner
    profile = CollectionScenarios().profile(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    (output / "receipt.json").write_text("{}", encoding="utf-8")
    (output / "profile.json").write_bytes(profile.read_bytes())
    (output / "run-contract.json").write_text("{}", encoding="utf-8")
    (output / "results.jsonl").write_text("", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    monkeypatch.setattr(runner, "load_runtime_env", lambda: None)
    for key in ("LEXMOUNT_SEARCH_API_KEY", "DEEPSEEK_API_KEY", "BOCHA_API_KEY"):
        monkeypatch.setenv(key, "fixture")
    monkeypatch.setattr(runner, "http_call", lambda *a, **kw: pytest.fail("Network called"))
    with pytest.raises(ValueError, match="completed|Completed"):
        runner.run(profile, output, resume=True, certified=True)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_invalid_billing_stops_before_any_paid_or_reference_call(tmp_path, monkeypatch):
    import pytest
    from harness.web_search import runner
    path = CollectionScenarios().profile(tmp_path)
    profile = json.loads(path.read_bytes())
    profile['methods'][0]['billing'] = 'typo'
    path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(runner, 'http_call', lambda *a, **kw: pytest.fail('Network called'))
    with pytest.raises(ValueError, match='billing policy'):
        runner.run(path, tmp_path / 'run', certified=True)
    assert not (tmp_path / 'run').exists()


def test_business_error_is_retained_and_retried_instead_of_crashing_billing(tmp_path, monkeypatch):
    from harness.web_search import runner
    path = CollectionScenarios().profile(tmp_path)
    profile = json.loads(path.read_bytes())
    profile['methods'] = [dict(profile['methods'][2], billing='http-200-nonempty')]
    profile['protocol']['retry'] = True
    path.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(runner, 'load_runtime_env', lambda: None)
    monkeypatch.setenv('BOCHA_API_KEY', 'fixture')
    def http(url, body=None, header=None, timeout=20):
        receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
        raw = b'{"code":429,"data":{"webPages":{"value":[]}}}' if body else b'reference ' * 20
        return receipt, raw
    monkeypatch.setattr(runner, 'http_call', http)
    runner.run(path, tmp_path / 'run')
    rows = [json.loads(line) for line in (tmp_path / 'run/results.jsonl').read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and len(rows[0]['attempts']) == 2
    assert rows[0]['error'] and rows[0]['results'] == [] and rows[0]['cost'] is None
    assert not json.loads((tmp_path / 'run/receipt.json').read_bytes())['certified']


def test_collection_receipt_binds_installed_scorer_and_detects_changed_sources(tmp_path, monkeypatch):
    from lexbench_websearch.inputs import implementation_sources
    from harness.web_search import runner
    sources = implementation_sources()
    prefix = 'lexbench_websearch/'
    original = runner.collection_implementation()
    assert prefix + 'scoring.py' in original
    for name, path in sources.items():
        assert original[prefix + name] == bytes_sha256(path.read_bytes())
    changed = tmp_path / 'scoring.py'
    changed.write_text('changed scorer', encoding="utf-8")
    for name, path in sources.items():
        destination = tmp_path / 'implementation' / prefix / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    monkeypatch.setattr('lexbench_websearch.inputs.implementation_sources', lambda: {**sources, 'scoring.py': changed})
    assert runner.collection_implementation() != original
    import pytest
    contract = {'implementation': {prefix + name: original[prefix + name] for name in sources}}
    with pytest.raises(ValueError, match='same live collection implementation'):
        runner.validate_resume_implementation(tmp_path, contract)


def test_collect_cli_passes_selected_supplemental_cohort(tmp_path, monkeypatch):
    from harness.web_search import runner
    observed = []
    monkeypatch.setattr(runner, 'run', lambda *a, **kw: observed.append(kw))
    runner.main(['--profile', str(tmp_path / 'instance.json'), '--output', str(tmp_path / 'run'), '--cohort', 'factual'])
    assert observed[0]['cohort'] == 'factual'


@pytest.mark.parametrize('superseded_partial', [False, True])
def test_execution_revision_preserves_or_archives_an_interrupted_retry(tmp_path, superseded_partial):
    import signal
    from harness.web_search import runner

    profile = execution_profile()
    profile['methods'] = profile['methods'][:2]
    profile['cases'] = profile['cases'][:1]
    profile['protocol'].update(order='bounded calls', rounds=1, concurrency=1, retry=True,
                               interval_s=0, max_calls_per_method=3)
    profile['execution_policy'] = {
        'purpose': 'quality', 'max_in_flight': 1, 'serial_reason': 'One provider account',
        'resources': {m['id']: {'capacity': 1, 'reason': 'One provider account'}
                      for m in profile['methods']}}
    source, output = tmp_path / 'profile.json', tmp_path / 'run'
    source.write_text(json.dumps(profile), encoding='utf-8')
    sends = []

    def http(url, body=None, header=None, timeout=20):
        receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
        if body is None:
            return receipt, b'reference ' * 20
        sends.append(url)
        if len(sends) == 1:
            signal.raise_signal(signal.SIGINT)
            return dict(receipt, http_status=503), b'{"error":"temporary"}'
        return receipt, b'{"results":[]}'

    with (patch.object(runner, 'http_call', side_effect=http),
          patch.object(runner, 'load_runtime_env'),
          patch.dict(os.environ, {m['key_env']: 'fixture' for m in profile['methods']})):
        with pytest.raises(KeyboardInterrupt):
            runner.run(source, output, certified=True)
        checkpoint = next((output / 'partial-results').glob('*.json'))
        partial = json.loads(checkpoint.read_text(encoding='utf-8'))['row']
        physical = partial['attempts'][0]['evidence']
        original = (output / physical['path']).read_bytes()
        old_bytes = (output / 'profile.json').read_bytes()
        changed = profile['methods'][0 if superseded_partial else 1]
        changed['endpoint'] = 'https://revised.example/search'
        revised = tmp_path / 'revised.json'
        revised.write_text(json.dumps(profile), encoding='utf-8')
        proof = tmp_path / 'proof.json'
        proof.write_text('{"interface_changed":true}', encoding='utf-8')
        revision = tmp_path / 'revision.json'
        revision.write_text(json.dumps({
            'schema': 'web-search-execution-revision/1', 'root_cause': 'Changed service endpoint',
            'superseded_methods': [changed['id']],
            'previous_profile_sha256': bytes_sha256(old_bytes),
            'profile_sha256': bytes_sha256(revised.read_bytes()),
            'evidence': [{'path': proof.name, 'sha256': bytes_sha256(proof.read_bytes())}]
        }), encoding='utf-8')
        runner.run(revised, output, certified=True, resume=True, execution_revision=revision)
    rows = [json.loads(line) for line in
            (output / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(sends) == 3 and len(rows) == 2
    first = next(row for row in rows if row['method'] == partial['method'])
    assert len(first['attempts']) == (1 if superseded_partial else 2)
    retained = output / physical['path']
    archived = output / 'resume-1' / physical['path']
    assert (archived if superseded_partial else retained).read_bytes() == original
    assert not list((output / 'partial-results').glob('*.json'))
    assert (output / 'resume-1/partial-results' / checkpoint.name).exists()


def test_account_denial_stops_other_retries_before_failure_finalization(tmp_path):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from harness.web_search import runner

    profile = execution_profile()
    profile['cases'] = profile['cases'][:1]
    profile['protocol'].update(order='bounded calls', rounds=1, concurrency=2, retry=True,
                               interval_s=0, max_calls_per_method=2)
    profile['execution_policy'] = {
        'purpose': 'quality', 'max_in_flight': 2,
        'resources': {m['id']: {'capacity': 1, 'reason': 'One provider account'}
                      for m in profile['methods']}}
    source, output = tmp_path / 'profile.json', tmp_path / 'run'
    source.write_text(json.dumps(profile), encoding='utf-8')
    started = threading.Barrier(2, timeout=3)
    finalizing, release, extra = threading.Event(), threading.Event(), threading.Event()
    sends = []

    def http(url, body=None, header=None, timeout=20):
        receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error='')
        if body is None:
            return receipt, b'reference ' * 20
        sends.append(url)
        if len(sends) > 2:
            extra.set()
            return receipt, b'{"results":[]}'
        started.wait()
        if url == profile['methods'][0]['endpoint']:
            return dict(receipt, http_status=402), b'{"error":"account"}'
        assert finalizing.wait(timeout=3)
        return dict(receipt, http_status=503), b'{"error":"temporary"}'

    def cost(method, status, *args):
        if status == 402:
            finalizing.set()
            assert release.wait(timeout=3)
        return None

    with (patch.object(runner, 'http_call', side_effect=http),
          patch.object(runner, 'request_cost', side_effect=cost),
          patch.object(runner, 'load_runtime_env'),
          patch.dict(os.environ, {m['key_env']: 'fixture' for m in profile['methods']}),
          ThreadPoolExecutor(max_workers=1) as executor):
        future = executor.submit(runner.run, source, output, certified=True)
        try:
            assert finalizing.wait(timeout=3)
            deadline = time.monotonic() + 2
            while not list((output / 'partial-results').glob('*.json')) and not extra.is_set():
                assert time.monotonic() < deadline
                time.sleep(.01)
            assert not extra.is_set() and len(sends) == 2
            assert len(list((output / 'reservations').glob('*.json'))) == 2
        finally:
            release.set()
        with pytest.raises(ValueError, match='Provider account error'):
            future.result(timeout=4)
    assert len(sends) == 2


@pytest.mark.parametrize('pages', [
    [None, {'url': 'https://example.org/page'}],
    [{'url': 'https://example.org/page'}] * 5 + [None],
])
def test_ai_answer_survives_invalid_webpage_slots_without_retry(tmp_path, pages):
    from harness.web_search.runner import run
    from harness.web_search.report import load_run

    profile = execution_profile()
    method = dict(profile['methods'][2], id='bocha-ai', adapter='bocha-ai',
                  output_options={'answer': True, 'stream': False})
    profile['methods'] = [method]
    profile['cases'] = profile['cases'][:1]
    profile['protocol'].update(normalize_top_k=True, result_url_resolution='request-url/2')
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(profile))
    payload = dict(code=200, messages=[
        dict(type='source', content_type='webpage', content=json.dumps({'value': pages})),
        dict(type='answer', content_type='text', content='A retained answer [引用:1]'),
    ])
    calls = []

    def http(url, body=None, *args, **kwargs):
        receipt = dict(http_status=200, elapsed_ms=10, transport_exit=0, transport_error='')
        if body is None:
            return receipt, b'reference ' * 20
        calls.append(body)
        return receipt, json.dumps(payload).encode()

    with (patch('harness.web_search.runner.http_call', side_effect=http),
          patch('harness.web_search.runner.load_runtime_env'),
          patch.dict(os.environ, BOCHA_API_KEY='fixture')):
        run(path, tmp_path/'run', certified=True)
    _, rows, _ = load_run(tmp_path/'run')
    assert len(calls) == 1
    assert not rows[0]['error']
    assert rows[0]['answer'] == 'A retained answer [引用:1]'
    assert len(rows[0]['results']) == min(5, len(pages))
    if pages[0] is None:
        assert rows[0]['results'][0]['invalid_slot']
