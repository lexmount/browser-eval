import hashlib
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.report import generate_webmainbench


PROVIDERS = ("kitesurf", "lightpanda", "obscura", "moli", "chromium")
METRICS = ("text_edit", "code_edit", "table_edit", "table_TEDS", "formula_edit")


class WebMainBenchReportTests(unittest.TestCase):
    def test_generates_matching_bilingual_route_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            case_id = "case-one"
            dataset = {
                "source_dataset_sha256": "a" * 64,
                "sample_manifest_sha256": "e" * 64,
                "coverage_plan": {"static_html": list(PROVIDERS)},
                "cases": [{
                    "id": case_id,
                    "url": "https://example.test/article",
                    "meta": {"language": "en", "level": "simple", "table": [], "code": [], "equation": []},
                    "input_html_sha256": "b" * 64,
                    "reference_sha256": hashlib.sha256(b"# Reference").hexdigest(),
                }],
            }
            (output / "references").mkdir()
            (output / "references" / f"{case_id}.md").write_text("# Reference", encoding="utf-8")
            (output / "sources").mkdir()
            (output / "sources" / f"{case_id}.html.txt").write_text("<h1>Frozen input</h1>", encoding="utf-8")
            records = []
            summaries = {}
            manifests = []
            for index, provider in enumerate(PROVIDERS, 1):
                score = index / 10
                records.append({
                    "provider": provider,
                    "case_id": case_id,
                    "attempt_id": f"{provider}__{case_id}__r001",
                    "run_id": f"run-{provider}",
                    "ok": True,
                    "total_elapsed_ms": index * 100,
                    "artifact_path": f"artifacts/{provider}/{case_id}.md",
                    "output_sha256": hashlib.sha256(f"output from {provider}".encode()).hexdigest(),
                    "physical_attempt_count": 1,
                    "metrics": {"overall": {"score": score}},
                })
                artifact = output / f"artifacts/{provider}/{case_id}.md"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text(f"output from {provider}", encoding="utf-8")
                summaries[provider] = {
                    "request_successes": 1,
                    "request_success_rate": 1.0,
                    "official_aggregate": {
                        "scores": {"overall": score, **{name: score for name in METRICS}},
                        "denominators": {name: 1 for name in METRICS},
                    },
                }
                manifests.append({
                    "provider": provider,
                    "started_at": "2026-09-14T00:00:00Z",
                    "completed_at": "2026-09-14T00:01:00Z",
                    "case_count": 1,
                    "timeout_seconds": 45,
                    "runner_sha256": "d" * 64,
                    "implementation_identity": {
                        "reported_version": "test",
                        "binary_sha256": "c" * 64,
                        "deployment": "fixture",
                    },
                })
            generate_webmainbench(
                output,
                dataset,
                records,
                summaries,
                manifests,
                {"status": "passed", "reviewed_at": "2026-09-14T00:00:00Z", "checks": []},
            )
            chinese = {path.relative_to(output).as_posix() for path in output.rglob("*.html") if "en" not in path.relative_to(output).parts}
            english = {path.relative_to(output / "en").as_posix() for path in (output / "en").rglob("*.html")}
            self.assertEqual(chinese, english)
            self.assertEqual(len(chinese), 21)
            self.assertIn("attempts/kitesurf__case-one__r001.html", chinese)
            self.assertIn('data-wmb-table', (output / "analysis/index.html").read_text(encoding="utf-8"))
            analysis = (output / "analysis/index.html").read_text(encoding="utf-8")
            analysis_en = (output / "en/analysis/index.html").read_text(encoding="utf-8")
            self.assertIn('id="wmb-compare-rows"', analysis)
            self.assertIn('id="wmb-compare-rows"', analysis_en)
            self.assertIn('data-delivered="kitesurf lightpanda obscura moli chromium"', analysis)
            self.assertIn('data-wmb-filter="request" data-wmb-default="failed"', analysis)
            self.assertIn('data-wmb-filter="request" data-wmb-default="failed"', analysis_en)
            self.assertIn('value="failed"', analysis)
            self.assertIn('value="success"', analysis)
            guide = (output / "guide/index.html").read_text(encoding="utf-8")
            guide_en = (output / "en/guide/index.html").read_text(encoding="utf-8")
            for anchor in ("background", "results", "examples", "configuration", "limits", "outlook"):
                self.assertIn(f'id="{anchor}"', guide)
                self.assertIn(f'id="{anchor}"', guide_en)
            self.assertIn('data-kind="reference"', (output / "attempts/kitesurf__case-one__r001.html").read_text(encoding="utf-8"))
            self.assertIn("WebMainBench-545", (output / "index.html").read_text(encoding="utf-8"))
            self.assertIn("independent review complete", (output / "en/index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
