import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from browser_harness_eval.models import EvalCase
from browser_harness_eval.providers.fixture import FixtureProvider
from browser_harness_eval.report import generate_report
from browser_harness_eval.runner import run_evaluation


class EndToEndTest(unittest.TestCase):
    def test_fixture_run_persists_failure_and_multipage_report(self) -> None:
        dataset = {
            "schema_version": 1,
            "id": "test",
            "name": "Test dataset",
            "tool": "web_fetch",
            "description": "Test",
            "cases": [
                {
                    "id": "ok",
                    "name": "OK case",
                    "url": "https://example.com",
                    "category": "static",
                    "expected_any": ["Expected"],
                },
                {
                    "id": "fail",
                    "name": "Failure case",
                    "url": "https://example.com/fail",
                    "category": "failure",
                    "expected_any": ["Never"],
                },
            ],
        }
        cases = [EvalCase(**item) for item in dataset["cases"]]
        provider = FixtureProvider("fixture", {"type": "fixture", "fail_cases": ["fail"]})
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            records, _ = run_evaluation(
                output_dir=run_dir,
                dataset_meta=dataset,
                dataset_path=Path("dataset.json"),
                cases=cases,
                providers=[provider],
                provider_config_path=Path("providers.json"),
                runs=1,
                timeout=1,
                delay=0,
            )
            report = generate_report(run_dir, dataset, records)
            self.assertTrue(report.exists())
            self.assertTrue((run_dir / "report/providers/fixture.html").exists())
            self.assertTrue((run_dir / "report/datasets/fail.html").exists())
            self.assertTrue((run_dir / "report/accounts/index.html").exists())
            self.assertTrue((run_dir / "report/environment/index.html").exists())
            self.assertTrue((run_dir / "report/methodology/index.html").exists())
            self.assertTrue((run_dir / "report/analysis/index.html").exists())
            self.assertTrue((run_dir / "report/execution/index.html").exists())
            self.assertFalse((run_dir / "report/review/index.html").exists())
            self.assertTrue((run_dir / "report/capabilities/index.html").exists())
            self.assertTrue((run_dir / "report/assets/review.js").exists())
            self.assertTrue((run_dir / "report/dataset.json").exists())
            attempt = run_dir / "report/attempts/fixture__fail__r001.html"
            self.assertIn("Configured fixture failure", attempt.read_text(encoding="utf-8"))
            self.assertIn("人工二次复核", attempt.read_text(encoding="utf-8"))
            analysis = (run_dir / "report/analysis/index.html").read_text(encoding="utf-8")
            self.assertIn('id="failure-provider"', analysis)
            self.assertIn('id="failure-category"', analysis)
            self.assertIn('id="failure-query"', analysis)
            self.assertIn("逐案例复核入口", analysis)
            index = report.read_text(encoding="utf-8")
            self.assertIn(">结果分析</a>", index)
            self.assertNotIn(">结果</a>", index)
            self.assertNotIn(">人工复核</a>", index)
            artifact = run_dir / "artifacts/fixture/fail/run-001.txt"
            self.assertIn("Fixture failure", artifact.read_text(encoding="utf-8"))
            failed_record = next(item for item in records if item["case_id"] == "fail")
            self.assertEqual(
                failed_record["metrics"]["content_sha256"],
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
            )
            summary = json.loads((run_dir / "summary.json").read_text())
            self.assertEqual(summary["providers"]["fixture"]["transport_success_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
