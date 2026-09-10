from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_certified_benchmark import main


class CertifiedBenchmarkTests(unittest.TestCase):
    def test_builds_mandatory_lifecycle_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{"id": "javascript-spa"}]}))
            output, work = root / "run", root / "work"

            def completed(command):
                if any(Path(value).name == "postcheck_headless.py" for value in command):
                    (work / "post-captures").mkdir(parents=True)
                    (work / "post-captures/observations.json").write_text("[]\n")

            with patch("scripts.run_certified_benchmark.run", side_effect=completed) as invoked:
                main(["--dataset", str(dataset), "--output", str(output),
                      "--work-root", str(work), "--providers", "p",
                      "--case-ids", "javascript-spa", "--browser-binary", "/chrome"])
            scripts = [Path(call.args[0][1]).name for call in invoked.call_args_list]
            self.assertEqual(scripts, [
                "collect_source_evidence.py", "capture_headless_browser_observations.py",
                "freeze_headless_references.py", "run_benchmark.py",
                "capture_headless_browser_observations.py", "postcheck_headless.py",
                "finalize_benchmark_run.py"])

    def test_requires_explicit_browser_for_rendered_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{"id": "javascript-spa"}]}))
            with self.assertRaises(SystemExit), patch("scripts.run_certified_benchmark.run") as invoked:
                main(["--dataset", str(dataset), "--output", str(root / "run"),
                      "--work-root", str(root / "work"), "--providers", "p",
                      "--case-ids", "javascript-spa"])
            invoked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
