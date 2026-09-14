import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import (
    IncompleteEvaluationError,
    copy_declared_static_evidence,
    run_evaluation,
)


class BarrierProvider(Provider):
    def __init__(self, name: str, barrier: threading.Barrier):
        super().__init__(name, {"type": "fixture"})
        self.barrier = barrier

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.barrier.wait(timeout=timeout)
        return ProviderResult(ok=True, content="Expected", status_code=200)


class RunnerConcurrencyTest(unittest.TestCase):
    def test_static_anchor_evidence_is_hash_bound_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "evaluation/dataset.json"
            source = root / "evaluation/sources/case/stable-page.txt"
            source.parent.mkdir(parents=True)
            source.write_text("Stable identity\n")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            (source.with_name("evidence.json")).write_text(json.dumps({
                "case_id": "case", "url": "https://example.com/",
                "stable_page_sha256": digest,
            }))
            case = EvalCase(
                "case", "Case", "https://example.com/", "challenge", [],
                source_benchmark={"stable_annotation": {
                    "anchor_evidence": "sources/case/stable-page.txt",
                    "anchor_evidence_sha256": digest,
                }},
            )
            output = root / "run"
            copy_declared_static_evidence(output, dataset, [case])
            self.assertEqual((output / "sources/case/stable-page.txt").read_bytes(), source.read_bytes())
            self.assertEqual(json.loads((output / "sources/case/evidence.json").read_text())["case_id"], "case")

            (source.with_name("evidence.json")).write_text(json.dumps({
                "case_id": "other", "url": "https://example.com/",
                "stable_page_sha256": digest,
            }))
            with self.assertRaisesRegex(ValueError, "evidence record is inconsistent"):
                copy_declared_static_evidence(root / "other-run", dataset, [case])

    def test_failed_task_is_called_once(self):
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
                provider_config_path=Path('providers.json'), runs=1, timeout=2, delay=0)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0]['metrics']['content_pass'])
            self.assertEqual((root / records[0]['artifact_path']).read_text(), 'wrong')
            self.assertNotIn('retry', records[0]['provider_metadata'])
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(manifest['attempt_count'], 1)
            self.assertNotIn('physical_attempt_count', manifest)

    def test_explicit_failure_retry_keeps_both_calls_and_uses_second(self):
        class Flaky(Provider):
            def __init__(self):
                super().__init__('flaky', {'type': 'fixture'})
                self.calls = 0

            def fetch(self, case, timeout):
                self.calls += 1
                return ProviderResult(True, 'wrong' if self.calls == 1 else 'Expected')

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
            self.assertEqual((root / retry['attempts'][0]['artifact_path']).read_text(), 'wrong')
            self.assertEqual((root / records[0]['artifact_path']).read_text(), 'Expected')
            manifest = json.loads((root / 'manifest.json').read_text())
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
            self.assertEqual((root / records[0]['artifact_path']).read_text(), 'failure-2')
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
            self.assertEqual(len((root/'results.jsonl').read_text().splitlines()),1)
            self.assertEqual(json.loads((root/'results.jsonl').read_text())['provider_metadata']['raw_response'],value)
            self.assertEqual((root/records[0]['artifact_path']).read_text(),value)

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
                records = [json.loads(line) for line in (run_dir/'results.jsonl').read_text().splitlines()]
                self.assertEqual(calls, ['case-0'])
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['error_type'], 'timeout')
                self.assertFalse(records[0]['ok'])
                self.assertEqual((run_dir/records[0]['artifact_path']).read_text(), '')
                manifest = json.loads((run_dir/'manifest.json').read_text())
                self.assertEqual(manifest['unexecuted'][0]['provider'], 'slow')
                self.assertEqual(manifest['attempt_count'], 1)
        finally:
            release.set()
            finished.wait(2)

    def test_provider_specific_cancel_grace_avoids_false_quarantine(self):
        calls = []

        class GracefulAfterCancel(Provider):
            @property
            def cancellation_grace_seconds(self):
                return 0.2

            def fetch(self, case, timeout):
                calls.append(case.id)
                time.sleep(0.04)
                return ProviderResult(True, 'late answer')

        cases = [EvalCase(f'case-{i}', 'Case', 'https://example.com', 'static', [])
                 for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            records, _ = run_evaluation(
                output_dir=Path(directory), dataset_meta={'id': 'fixture', 'cases': []},
                dataset_path=Path('dataset.json'), cases=cases,
                providers=[GracefulAfterCancel('slow', {})],
                provider_config_path=Path('providers.json'), runs=1,
                timeout=.02, delay=0,
            )
        self.assertEqual(calls, ['case-0', 'case-1'])
        self.assertEqual([row['error_type'] for row in records], ['timeout', 'timeout'])

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
                manifest = json.loads((root / 'manifest.json').read_text())
                self.assertEqual(manifest['unexecuted'], [])
        finally:
            release.set()

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
