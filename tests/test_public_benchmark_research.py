import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.report_build import build_public_benchmark_research


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "research/public-web-benchmarks.json"


class PublicBenchmarkResearchTest(unittest.TestCase):
    def test_contract_has_no_results_and_no_duplicate_legacy_corpora(self):
        research = json.loads(SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(research["status"], "research_only")
        self.assertEqual(research["measured_results"], 0)
        self.assertEqual(
            research["decision"]["primary"],
            ["webmainbench-545"],
        )
        wcxb = next(row for row in research["direct_benchmarks"] if row["id"] == "wcxb")
        self.assertEqual(sum(count for _, count in wcxb["splits"]), 2008)
        self.assertEqual(wcxb["repository_audit"]["development_html"], 1497)
        self.assertEqual(wcxb["repository_audit"]["test_ground_truth"], 511)
        self.assertEqual(wcxb["repository_audit"]["empty_main_content"], 27)
        self.assertEqual(wcxb["repository_audit"]["published_test_result_rows"], 5)
        selected = next(row for row in research["direct_benchmarks"] if row["id"] == "webmainbench-545")
        self.assertEqual(selected["fit"], "selected")
        self.assertEqual(research["sampling_contract"]["size"], 200)
        wceb = next(row for row in research["direct_benchmarks"] if row["id"] == "wceb")
        self.assertEqual(sum(count for _, count in wceb["subsets"]), 3985)
        dispositions = " ".join(row["disposition"] for row in research["embedded_or_supplementary"])
        self.assertIn("Do not score twice", dispositions)
        for row in research["direct_benchmarks"] + research["adjacent_browser_benchmarks"]:
            self.assertTrue(row["sources"])
            self.assertTrue(all(url.startswith("https://") for url in row["sources"]))

    def test_build_has_bilingual_route_parity_and_research_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "report"
            index = build_public_benchmark_research(SOURCE, output)
            self.assertEqual(index, output.resolve() / "index.html")
            routes = ["index.html"] + [
                route
                for route in (
                    "datasets/index.html",
                    "methodology/index.html",
                    "analysis/index.html",
                    "review/index.html",
                    "guide/index.html",
                    "adoption/index.html",
                    "reproduce/index.html",
                    "changes/index.html",
                    "accounts/index.html",
                )
            ]
            for route in routes:
                self.assertTrue((output / route).is_file(), route)
                self.assertTrue((output / "en" / route).is_file(), "en/" + route)
            chinese = index.read_text(encoding="utf-8")
            english = (output / "en/index.html").read_text(encoding="utf-8")
            self.assertIn("调研版 · 0 条实测结果", chinese)
            self.assertIn("Research edition · 0 measured results", english)
            self.assertNotIn("综合通过率总榜", chinese)
            english_datasets = (output / "en/datasets/index.html").read_text(encoding="utf-8")
            self.assertIn("<th>No.</th>", english_datasets)
            self.assertNotIn("<th>序号</th>", english_datasets)
            self.assertIn("唯一选择 WebMainBench-545", (output / "guide/index.html").read_text(encoding="utf-8"))
            self.assertIn("test result rows: 5/511", (output / "en/review/index.html").read_text(encoding="utf-8"))
            rendered_model = json.loads((output / "research.json").read_text())
            self.assertEqual(rendered_model["decision"], json.loads(SOURCE.read_text())["decision"])
            self.assertEqual(rendered_model["frozen_sample"]["case_count"], 200)
            self.assertTrue((output / "sample-manifest.json").is_file())

    def test_does_not_replace_unmarked_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "report"
            output.mkdir()
            with self.assertRaisesRegex(ValueError, "unmarked"):
                build_public_benchmark_research(SOURCE, output)


if __name__ == "__main__":
    unittest.main()
