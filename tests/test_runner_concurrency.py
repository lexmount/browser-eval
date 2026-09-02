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
