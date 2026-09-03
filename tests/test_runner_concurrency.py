import json
import tempfile
import threading
import unittest
from pathlib import Path

from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation


class BarrierProvider(Provider):
    def __init__(self, name: str, barrier: threading.Barrier):
        super().__init__(name, {"type": "fixture"})
        self.barrier = barrier

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.barrier.wait(timeout=timeout)
        return ProviderResult(ok=True, content="Expected", status_code=200)


class RunnerConcurrencyTest(unittest.TestCase):
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
                records, _ = run_evaluation(output_dir=run_dir,
                    dataset_meta={'id':'fixture','cases':[]}, dataset_path=Path('dataset.json'),
                    cases=cases, providers=[Unstoppable('slow', {})],
                    provider_config_path=Path('providers.json'), runs=2, timeout=.02, delay=0)
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
