from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.finalize_benchmark_run import finalize_aggregate


class FinalizeBenchmarkRunTests(unittest.TestCase):
    def write(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def test_updates_top_level_batches_and_merges_certificates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = {"attempt_ids": ["p__a__r001"], "reference_checks": {"status": "awaiting_postcheck"}}
            second = {"attempt_ids": ["p__b__r001"]}
            self.write(root / "execution.json", {"batches": [first, second]})
            paths = [root / "workers/p/web/manifest.json", root / "workers/p/resources/manifest.json"]
            self.write(paths[0], first)
            self.write(paths[1], second)
            observations = root / "observations.json"
            observations.write_text("[]\n")

            def finalize(worker: Path, _observations: Path) -> dict:
                manifest = json.loads((worker / "manifest.json").read_text())
                manifest["reference_checks"] = {"status": "verified"}
                self.write(worker / "manifest.json", manifest)
                certificate = worker / "reference-checks/observations-a.json"
                certificate.parent.mkdir()
                certificate.write_text("evidence\n")
                return {"status": "verified"}

            with patch("scripts.finalize_benchmark_run.finalize_reference_checks", side_effect=finalize):
                finalize_aggregate(root, observations)

            batches = json.loads((root / "execution.json").read_text())["batches"]
            self.assertEqual(batches[0]["reference_checks"]["status"], "verified")
            self.assertNotIn("reference_checks", batches[1])
            self.assertEqual((root / "reference-checks/observations-a.json").read_text(), "evidence\n")

    def test_refuses_worker_inventory_mismatch_before_finalizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root / "execution.json", {"batches": [{"attempt_ids": ["p__a__r001"]}]})
            self.write(root / "workers/p/web/manifest.json", {"attempt_ids": ["p__b__r001"]})
            observations = root / "observations.json"
            observations.write_text("[]\n")
            with patch("scripts.finalize_benchmark_run.finalize_reference_checks") as finalize:
                with self.assertRaisesRegex(ValueError, "differ"):
                    finalize_aggregate(root, observations)
            finalize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
