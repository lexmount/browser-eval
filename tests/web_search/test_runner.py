from tests.web_search.fixtures import execution_profile
"""Collection scenarios: missing sources, empty billing, and retained failures."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.record_io import bytes_sha256
from harness.web_search.report import load_run
from harness.web_search.runner import run


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
