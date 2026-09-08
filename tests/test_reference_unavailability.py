import json
from pathlib import Path
import tempfile
import unittest

from scripts.apply_reference_unavailability import apply


class ReferenceUnavailabilityTests(unittest.TestCase):
    def test_preserves_task_checks_but_removes_snapshot_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dataset = root / "dataset.json"
            dataset.write_text(json.dumps({"cases": [{
                "id": "blocked", "ground_truth_main_content": "old body",
                "required_facts": [{"id": "fact", "alternatives": ["stable"]}],
                "body_requirements": {"version": 1},
            }]}))
            captures = root / "captures"
            captures.mkdir()
            (captures / "audit.json").write_text(json.dumps([{
                "case_id": "blocked", "checked_at": "2026-09-04T00:00:00+00:00",
                "status": 504, "sha256": "response", "missing_facts": ["fact"],
            }]))
            apply(dataset, captures, {"blocked"})
            case = json.loads(dataset.read_text())["cases"][0]
            self.assertEqual(case["ground_truth_main_content"], "")
            self.assertNotIn("body_requirements", case)
            self.assertEqual(case["required_facts"][0]["id"], "fact")
            self.assertEqual(case["source_verification"]["state"], "unavailable")
            self.assertTrue((root / "sources/blocked/unavailable-reference.json").is_file())


if __name__ == "__main__":
    unittest.main()
