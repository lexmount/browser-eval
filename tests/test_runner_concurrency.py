import json
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import (
    IncompleteEvaluationError, preflight_declared_capacity, run_evaluation,
    classify_exception,
)


class BarrierProvider(Provider):
    def __init__(self, name: str, barrier: threading.Barrier):
        super().__init__(name, {"type": "fixture"})
        self.barrier = barrier

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.barrier.wait(timeout=timeout)
        return ProviderResult(ok=True, content="Expected", status_code=200)


class RunnerConcurrencyTest(unittest.TestCase):
    def test_serial_interrupt_retains_the_current_response_without_another_call(self):
        import signal
        from unittest.mock import patch

        calls = []

        def fetch(provider, case, *args, **kwargs):
            calls.append(case.id)
            if len(calls) == 1:
                signal.raise_signal(signal.SIGINT)
            return ProviderResult(True, 'Expected'), None

        cases = [EvalCase(str(i), 'Case', 'https://example.com', 'static', ['Expected'])
                 for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp, \
                patch('harness.web_fetch.runner.fetch_with_deadline', side_effect=fetch):
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(output_dir=Path(tmp), dataset_meta={'id': 'fixture', 'cases': []},
                               dataset_path=Path('dataset.json'), cases=cases,
                               providers=[BarrierProvider('one', threading.Barrier(1))],
                               provider_config_path=Path('providers.json'), runs=1, timeout=1,
                               delay=0, provider_concurrency=1)
            retained = (Path(tmp) / 'results.jsonl').read_text(encoding='utf-8').splitlines()
            manifest = json.loads((Path(tmp) / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['attempt_count'], 1)
            self.assertEqual(manifest['attempt_ids'], [json.loads(retained[0])['attempt_id']])
            self.assertEqual(manifest['execution_status'], 'interrupted')
            self.assertTrue(manifest['completed_at'])
            self.assertEqual({r['case_id'] for r in manifest['unexecuted'] if 'case_id' in r}, {'1', '2'})
        self.assertEqual(calls, ['0'])
        self.assertEqual(len(retained), 1)
        self.assertTrue(json.loads(retained[0])['ok'])

    def test_operator_interrupt_stops_batches_after_current_calls(self):
        import signal
        from unittest.mock import patch

        from harness import benchmark_execution

        ready = threading.Barrier(3, timeout=3)
        release = threading.Event()
        calls = []
        retained = []
        original_wait = benchmark_execution.wait
        interrupted = False

        def fetch(provider, case, *args, **kwargs):
            calls.append((provider.name, case.id))
            ready.wait()
            self.assertTrue(release.wait(timeout=3))
            return ProviderResult(True, 'Expected'), None

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

        providers = [BarrierProvider(name, threading.Barrier(1)) for name in ('one', 'two')]
        cases = [EvalCase(str(i), 'Case', 'https://example.com', 'static', ['Expected'])
                 for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            with patch('harness.web_fetch.runner.fetch_with_deadline', side_effect=fetch), \
                    patch.object(benchmark_execution, 'wait', side_effect=wait_once):
                with self.assertRaises(KeyboardInterrupt):
                    run_evaluation(output_dir=Path(tmp), dataset_meta={'id': 'fixture', 'cases': []},
                                   dataset_path=Path('dataset.json'), cases=cases, providers=providers,
                                   provider_config_path=Path('providers.json'), runs=1, timeout=1,
                                   delay=0, provider_concurrency=2)
            retained = (Path(tmp) / 'results.jsonl').read_text(encoding="utf-8").splitlines()
            manifest = json.loads((Path(tmp) / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['attempt_count'], 2)
            self.assertEqual(set(manifest['attempt_ids']),
                             {json.loads(row)['attempt_id'] for row in retained})
            self.assertEqual(manifest['execution_status'], 'interrupted')
            self.assertTrue(manifest['completed_at'])
            self.assertEqual({r['case_id'] for r in manifest['unexecuted'] if 'case_id' in r}, {'1', '2'})
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(retained), 2)
        self.assertTrue(all(json.loads(row)['ok'] for row in retained))

    def test_conflicting_shared_limits_are_rejected_before_clamping(self):
        from unittest.mock import patch

        providers = [BarrierProvider('one', threading.Barrier(1)),
                     BarrierProvider('two', threading.Barrier(1))]
        for provider, capacity in zip(providers, [2, 3], strict=True):
            provider.config['execution_resources'] = {
                'account': {'capacity': capacity, 'reason': 'Shared account'}}
        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        with tempfile.TemporaryDirectory() as tmp, patch(
            'harness.web_fetch.runner.fetch_with_deadline'
        ) as call:
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                run_evaluation(output_dir=Path(tmp), dataset_meta={'id': 'fixture', 'cases': []},
                               dataset_path=Path('dataset.json'), cases=[case], providers=providers,
                               provider_config_path=Path('providers.json'), runs=1, timeout=1,
                               delay=0, provider_concurrency=2)
            call.assert_not_called()

    def test_failed_cleanup_stops_queued_variants_of_the_shared_service(self):
        from unittest.mock import patch

        from harness.web_fetch.providers.base import ProcessCleanupError

        config = {'type': 'fixture', 'execution_resources': {
            'one-service': {'capacity': 1, 'reason': 'Shared mutable service'}}}
        providers = [BarrierProvider('one', threading.Barrier(1)),
                     BarrierProvider('two', threading.Barrier(1))]
        for provider in providers:
            provider.config.update(config)
        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        with tempfile.TemporaryDirectory() as tmp, patch(
            'harness.web_fetch.runner.fetch_with_deadline',
            side_effect=ProcessCleanupError('Worker cleanup is unresolved'),
        ) as call:
            with self.assertRaises(IncompleteEvaluationError):
                run_evaluation(output_dir=Path(tmp), dataset_meta={'id': 'fixture', 'cases': []},
                               dataset_path=Path('dataset.json'), cases=[case], providers=providers,
                               provider_config_path=Path('providers.json'), runs=1, timeout=1,
                               delay=0, provider_concurrency=2)
            self.assertEqual(call.call_count, 1)
            rows = [json.loads(line) for line in (Path(tmp) / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([row['provider'] for row in rows], ['one'])

    def test_quarantine_stops_already_active_batches_before_their_next_call(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch import runner
        from harness.web_fetch.providers.base import ProcessCleanupError

        ready = threading.Barrier(2, timeout=3)
        release = threading.Event()
        calls = []

        class StopEvent(threading.Event):
            def set(self):
                super().set()
                release.set()

        def fetch(provider, case, *args, **kwargs):
            calls.append((provider.name, case.id))
            ready.wait()
            if provider.name == 'one':
                raise ProcessCleanupError('Worker remains alive')
            self.assertTrue(release.wait(3))
            return ProviderResult(True, 'Expected'), None

        providers = [BarrierProvider(name, threading.Barrier(1)) for name in ('one', 'two')]
        cases = [EvalCase(str(i), 'Case', 'https://example.com', 'static', ['Expected'])
                 for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(runner, 'threading', SimpleNamespace(Event=StopEvent, Lock=threading.Lock)), \
                patch.object(runner, 'fetch_with_deadline', side_effect=fetch):
            with self.assertRaises(IncompleteEvaluationError):
                run_evaluation(output_dir=Path(tmp), dataset_meta={'id': 'fixture', 'cases': []},
                               dataset_path=Path('dataset.json'), cases=cases, providers=providers,
                               provider_config_path=Path('providers.json'), runs=1, timeout=1,
                               delay=0, provider_concurrency=2, max_failure_retries=1)
            rows = [json.loads(line) for line in
                    (Path(tmp) / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(sorted(calls), [('one', '0'), ('two', '0')])
        self.assertEqual(len(rows), 2)
        self.assertTrue(next(row for row in rows if row['provider'] == 'two')['ok'])

    def test_shared_service_capacity_prevents_overlapping_provider_variants(self):
        import time

        lock = threading.Lock()
        active = 0
        maximum = 0

        class Shared(Provider):
            def fetch(self, case, timeout):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.02)
                with lock:
                    active -= 1
                return ProviderResult(ok=True, content='Expected', status_code=200)

        config = {'type': 'fixture', 'execution_resources': {
            'one-service': {'capacity': 1, 'reason': 'Shared mutable service'}}}
        providers = [Shared('one', config), Shared('two', config)]
        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = run_evaluation(
                output_dir=Path(tmp), dataset_meta={'id': 'fixture', 'cases': []},
                dataset_path=Path('dataset.json'), cases=[case], providers=providers,
                provider_config_path=Path('providers.json'), runs=1, timeout=1,
                delay=0, provider_concurrency=2)
            manifest = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(maximum, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(manifest['execution_policy']['resources']['shared/one-service']['capacity'], 1)

    def test_certificate_and_transport_errors_are_classified(self):
        certificate = ssl.SSLCertVerificationError(1, "certificate verify failed")
        for error, expected in (
            (certificate, "tls_certificate"),
            (urllib.error.URLError(certificate), "tls_certificate"),
            (urllib.error.URLError(socket.gaierror("DNS lookup failed")), "dns"),
            (urllib.error.URLError(TimeoutError("read timed out")), "timeout"),
        ):
            with self.subTest(error=error):
                kind, message = classify_exception(error)
                self.assertEqual(kind, expected)
                self.assertTrue(message)

    def test_deadline_retains_cancelled_process_diagnostics_never_late_answer(self):
        from harness.web_fetch.call_boundary import fetch_with_deadline
        class Cancelled(Provider):
            def __init__(self):
                super().__init__('cancelled',{})
                self.release=threading.Event()
            @property
            def cancellation_cleanup_seconds(self):return .2
            def fetch(self,case,timeout):
                self.release.wait(1)
                return ProviderResult(True,'late answer',metadata={
                    'exit_code':-9,'stderr':'connection pending before cancellation',
                    'stderr_bytes':38,'stdout_bytes':11,'reported_version':'fixture 1',
                    '_answer_elapsed_ms':99999})
            def cancel(self):self.release.set()
        case=EvalCase('case','Case','https://example.com','static',[])
        result,worker=fetch_with_deadline(Cancelled(),case,.02)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result.ok)
        self.assertEqual(result.content,'')
        self.assertEqual(result.error_type,'timeout')
        self.assertEqual(result.metadata['exit_code'],-9)
        self.assertIn('connection pending',result.metadata['stderr'])
        self.assertLess(result.metadata['_answer_elapsed_ms'],100)

    def test_discarded_late_file_remains_a_measured_failure(self):
        from harness.web_fetch.call_boundary import fetch_with_deadline
        from lexbench_webfetch.scoring import score_attempt
        from lexbench_webfetch.metrics import task_measured, task_passed
        class LateFile(Provider):
            def __init__(self):
                super().__init__('late', {})
                self.release = threading.Event()
            @property
            def cancellation_cleanup_seconds(self):
                return .2
            def cancel(self):
                self.release.set()
            def fetch(self, case, timeout):
                self.release.wait(1)
                return ProviderResult(True, '', files=[{'data': b'\x89PNG\r\n\x1a\n'+b'x'*40}],
                    metadata={'images': ['https://example.test/late.png']})
        case = EvalCase('case', 'Case', 'https://example.test/file', 'static', [], task_kind='file')
        result, worker = fetch_with_deadline(LateFile(), case, .02)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result.error_type, 'timeout')
        self.assertEqual(result.files, [])
        self.assertNotIn('images', result.metadata)
        metrics = score_attempt(case, b'', ok=result.ok, metadata=result.metadata)
        self.assertFalse(metrics['raw_file_pass'])
        self.assertTrue(task_measured(metrics))
        self.assertFalse(task_passed(result.ok, metrics))

    def test_capacity_hook_is_optional_and_runs_once_per_logical_provider(self):
        calls = []

        class Capacity(Provider):
            def preflight_capacity(self, declared_calls, timeout=10):
                calls.append((self.name, declared_calls, timeout))

            def fetch(self, case, timeout):
                raise AssertionError('capacity preflight must not fetch')

        providers = [
            Capacity('metered', {'scope': 'web'}),
            Capacity('metered', {'scope': 'resources'}),
            BarrierProvider('without-hook', threading.Barrier(1)),
        ]

        preflight_declared_capacity(providers, declared_calls=24, timeout=7)

        self.assertEqual(calls, [('metered', 24, 7)])

    def test_capacity_preflight_passes_exact_case_credit_estimate(self):
        calls = []

        class Capacity(Provider):
            def capacity_credits_for_cases(
                self, cases, *, runs, max_failure_retries
            ):
                calls.append(("estimate", [case.id for case in cases], runs,
                              max_failure_retries))
                return 19

            def preflight_capacity(
                self, declared_calls, timeout=10, *, required_credits=None
            ):
                calls.append(("preflight", declared_calls, timeout, required_credits))

            def fetch(self, case, timeout):
                raise AssertionError("capacity preflight must not fetch")

        cases = [
            EvalCase("one", "One", "https://example.com/1", "static", []),
            EvalCase("two", "Two", "https://example.com/2", "static", []),
        ]
        preflight_declared_capacity(
            [Capacity("metered", {})],
            declared_calls=8,
            timeout=7,
            cases=cases,
            runs=2,
            max_failure_retries=1,
        )

        self.assertEqual(calls, [
            ("estimate", ["one", "two"], 2, 1),
            ("preflight", 8, 7, 19),
        ])

    def test_failed_preflight_creates_no_evidence_or_calls(self):
        class Invalid(Provider):
            def validate(self):
                return ["wrong revision"]

            def fetch(self, case, timeout):
                raise AssertionError("fetch must not be called")

        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'run'
            with self.assertRaisesRegex(ValueError, 'wrong revision'):
                run_evaluation(output_dir=output, dataset_meta={'id': 'test'},
                    dataset_path=Path('dataset.json'), cases=[case],
                    providers=[Invalid('invalid', {})],
                    provider_config_path=Path('providers.json'), runs=1, timeout=2, delay=0)
            self.assertFalse(output.exists())

    def test_manifest_records_identity_created_by_preflight(self):
        class Identified(Provider):
            def validate(self):
                self._implementation_identity = {
                    "repository": {"head": "a" * 40, "tracked_status": ""},
                    "files_sha256": {"repo/worker.py": "b" * 64},
                }
                return []

            def fetch(self, case, timeout):
                return ProviderResult(True, "Expected")

        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_evaluation(output_dir=root, dataset_meta={'id': 'test'},
                dataset_path=Path('dataset.json'), cases=[case],
                providers=[Identified('identified', {})],
                provider_config_path=Path('providers.json'), runs=1, timeout=2, delay=0)
            manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
            identity = manifest['providers'][0]['implementation_identity']
            self.assertEqual(identity['repository']['head'], 'a' * 40)

    def test_zero_retries_calls_failed_task_once(self):
        class Flaky(Provider):
            def __init__(self):
                super().__init__('flaky', {'type': 'fixture'})
                self.calls = 0

            def fetch(self, case, timeout):
                self.calls += 1
                return ProviderResult(True, 'wrong')

        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        provider = Flaky()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(output_dir=root, dataset_meta={'id': 'test'},
                dataset_path=Path('dataset.json'), cases=[case], providers=[provider],
                provider_config_path=Path('providers.json'), runs=1, timeout=2, delay=0,
                max_failure_retries=0)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0]['metrics']['content_pass'])
            self.assertEqual((root / records[0]['artifact_path']).read_text(encoding='utf-8'), 'wrong')
            self.assertNotIn('retry', records[0]['provider_metadata'])
            manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['attempt_count'], 1)
            self.assertNotIn('physical_attempt_count', manifest)

    def test_transient_failure_retry_keeps_both_calls_and_uses_second(self):
        class Flaky(Provider):
            def __init__(self):
                super().__init__('flaky', {'type': 'fixture'})
                self.calls = 0

            def fetch(self, case, timeout):
                self.calls += 1
                if self.calls == 1:
                    return ProviderResult(False, '', error_type='network')
                return ProviderResult(True, 'Expected')

        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        provider = Flaky()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(output_dir=root, dataset_meta={'id': 'test'},
                dataset_path=Path('dataset.json'), cases=[case], providers=[provider],
                provider_config_path=Path('providers.json'), runs=1, timeout=2, delay=0,
                max_failure_retries=1)
            self.assertEqual(provider.calls, 2)
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]['metrics']['content_pass'])
            retry = records[0]['provider_metadata']['retry']
            self.assertEqual(retry['physical_attempts'], 2)
            self.assertEqual(retry['retries_used'], 1)
            self.assertEqual([item['task_pass'] for item in retry['attempts']], [False, True])
            self.assertEqual((root / retry['attempts'][0]['artifact_path']).read_text(encoding='utf-8'), '')
            self.assertEqual((root / records[0]['artifact_path']).read_text(encoding='utf-8'), 'Expected')
            manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['attempt_count'], 1)
            self.assertEqual(manifest['parameters']['max_failure_retries'], 1)

    def test_explicit_failure_retry_never_selects_better_first_call(self):
        class TwiceFailed(Provider):
            def __init__(self):
                super().__init__('twice-failed', {'type': 'fixture'})
                self.calls = 0

            def fetch(self, case, timeout):
                self.calls += 1
                return ProviderResult(False, f'failure-{self.calls}', error_type='network')

        case = EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])
        provider = TwiceFailed()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(output_dir=root, dataset_meta={'id': 'test'},
                dataset_path=Path('dataset.json'), cases=[case], providers=[provider],
                provider_config_path=Path('providers.json'), runs=1, timeout=2, delay=0,
                max_failure_retries=1)
            self.assertEqual(provider.calls, 2)
            self.assertEqual((root / records[0]['artifact_path']).read_text(encoding='utf-8'), 'failure-2')
            self.assertEqual(records[0]['provider_metadata']['retry']['selection'],
                             'final physical attempt is authoritative; no best-of selection')

    def test_jsonl_escapes_unicode_line_separators_without_changing_output(self):
        value = 'before\u0085middle\u2028after\u2029end'
        class TextProvider(Provider):
            def fetch(self,case,timeout):return ProviderResult(True,value,metadata={'raw_response':value})
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            records,_=run_evaluation(output_dir=root,dataset_meta={'id':'test'},dataset_path=Path('dataset.json'),
                cases=[EvalCase('case','Case','https://example.com','static',[])],providers=[TextProvider('text',{})],
                provider_config_path=Path('providers.json'),runs=1,timeout=2,delay=0)
            self.assertEqual(len((root/'results.jsonl').read_text(encoding='utf-8').splitlines()),1)
            self.assertEqual(json.loads((root/'results.jsonl').read_text(encoding='utf-8'))['provider_metadata']['raw_response'],value)
            self.assertEqual((root/records[0]['artifact_path']).read_text(encoding='utf-8'),value)

    def test_unstoppable_timeout_is_quarantined_not_counted_as_remaining_failures(self):
        release = threading.Event()
        finished = threading.Event()
        calls = []

        class Unstoppable(Provider):
            def fetch(self, case, timeout):
                calls.append(case.id)
                release.wait(2)
                finished.set()
                return ProviderResult(True, 'late answer')

        cases = [EvalCase(f'case-{i}', 'Case', 'https://example.com', 'static', []) for i in range(2)]
        try:
            with tempfile.TemporaryDirectory() as directory:
                run_dir = Path(directory)
                with self.assertRaisesRegex(IncompleteEvaluationError, '1/4 attempts'):
                    run_evaluation(output_dir=run_dir,
                        dataset_meta={'id':'fixture','cases':[]}, dataset_path=Path('dataset.json'),
                        cases=cases, providers=[Unstoppable('slow', {})],
                        provider_config_path=Path('providers.json'), runs=2, timeout=.02, delay=0)
                records = [json.loads(line) for line in (run_dir/'results.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(calls, ['case-0'])
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['error_type'], 'timeout')
                self.assertFalse(records[0]['ok'])
                self.assertEqual((run_dir/records[0]['artifact_path']).read_text(encoding='utf-8'), '')
                manifest = json.loads((run_dir/'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(manifest['unexecuted'][0]['provider'], 'slow')
                self.assertEqual(manifest['attempt_count'], 1)
        finally:
            release.set()
            finished.wait(2)

    def test_unstoppable_timeout_on_final_case_is_complete(self):
        release = threading.Event()

        class Unstoppable(Provider):
            def fetch(self, case, timeout):
                release.wait(2)
                return ProviderResult(True, 'late answer')

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                records, _ = run_evaluation(
                    output_dir=root, dataset_meta={'id': 'fixture', 'cases': []},
                    dataset_path=Path('dataset.json'),
                    cases=[EvalCase('only', 'Only', 'https://example.com', 'static', [])],
                    providers=[Unstoppable('slow', {})],
                    provider_config_path=Path('providers.json'), runs=1,
                    timeout=.02, delay=0,
                )
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['error_type'], 'timeout')
                manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(manifest['unexecuted'], [])
        finally:
            release.set()

    def test_cancellable_timeout_cleans_up_and_continues_without_latency_inflation(self):
        calls = []

        class Cancellable(Provider):
            def __init__(self, name, config):
                super().__init__(name, config)
                self.release = threading.Event()

            @property
            def cancellation_cleanup_seconds(self):
                return .2

            def begin_call(self):
                self.release.clear()

            def fetch(self, case, timeout):
                calls.append(case.id)
                self.release.wait(1)
                raise TimeoutError('cancelled')

            def cancel(self):
                self.release.set()

        cases = [EvalCase(f'case-{i}', 'Case', 'https://example.com', 'static', []) for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, _ = run_evaluation(
                output_dir=root, dataset_meta={'id': 'fixture', 'cases': []},
                dataset_path=Path('dataset.json'), cases=cases,
                providers=[Cancellable('slow', {})], provider_config_path=Path('providers.json'),
                runs=1, timeout=.02, delay=0,
            )
        self.assertEqual(calls, ['case-0', 'case-0', 'case-1', 'case-1'])
        self.assertEqual([record['error_type'] for record in records], ['timeout', 'timeout'])
        self.assertTrue(all(record['elapsed_ms'] < 100 for record in records))

    def test_providers_overlap_but_results_remain_deterministic(self) -> None:
        barrier = threading.Barrier(2)
        providers = [BarrierProvider("first", barrier), BarrierProvider("second", barrier)]
        case = EvalCase(
            id="case",
            name="Case",
            url="https://example.com",
            category="static",
            expected_any=["Expected"],
        )
        dataset = {"id": "parallel", "name": "Parallel", "cases": []}
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            records, _ = run_evaluation(
                output_dir=run_dir,
                dataset_meta=dataset,
                dataset_path=Path("dataset.json"),
                cases=[case],
                providers=providers,
                provider_config_path=Path("providers.json"),
                runs=1,
                timeout=1,
                delay=0,
                provider_concurrency=2,
            )
            self.assertEqual([item["provider"] for item in records], ["first", "second"])
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["parameters"]["provider_concurrency"], 2)
            self.assertEqual(
                manifest["parameters"]["concurrency_scope"],
                "providers_parallel_cases_sequential_per_provider",
            )


if __name__ == "__main__":
    unittest.main()
