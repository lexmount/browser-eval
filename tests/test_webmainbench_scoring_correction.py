import json
from pathlib import Path
import unittest

from scripts.correct_webmainbench_scoring import CONTROL_IDS, REGISTRY, revised_metrics


class FakeResult:
    def __init__(self, score=0.0, success=True):
        self.score = score
        self.success = success
        self.details = {}
        self.error_message = "unavailable" if not success else None


class FakeMetric:
    def __init__(self, available=True, score=0.5):
        self.available = available
        self.score = score

    def calculate(self, *args, **kwargs):
        return FakeResult(self.score, self.available)

    def _extract_formula_content(self, *args):
        return "x" if self.available else ""

    def _extract_table_content(self, *args):
        return "table" if self.available else ""


class FakeScorer:
    def __init__(self):
        self.metrics = {
            "text_edit": FakeMetric(score=0.5),
            "code_edit": FakeMetric(score=0.25),
            "formula_edit": FakeMetric(available=False),
            "table_edit": FakeMetric(available=True),
        }


class WebMainBenchScoringCorrectionTests(unittest.TestCase):
    def test_failure_is_zero_inside_reference_population(self):
        old = {
            "table_edit": {"score": 0.8, "success": True, "details": {}},
            "table_TEDS": {"score": 0.7, "success": True, "details": {}},
        }
        result = revised_metrics(old, FakeScorer(), "", "reference", {"code": ["inline"]}, False)
        self.assertEqual(result["text_edit"]["score"], 0.0)
        self.assertEqual(result["table_edit"]["score"], 0.0)
        self.assertEqual(result["table_TEDS"]["score"], 0.0)
        self.assertFalse(result["formula_edit"]["success"])

    def test_track_controls_are_paired_by_root_cause(self):
        document = json.loads(Path(REGISTRY).read_text())
        self.assertEqual({row["id"] for row in document["cases"]}, set(CONTROL_IDS))
        modes = {}
        for row in document["cases"]:
            modes.setdefault(row["root_cause"], set()).add(row["failure_mode"])
        self.assertTrue(modes)
        self.assertTrue(all(value == {"false-positive", "false-negative"}
                            for value in modes.values()))


if __name__ == "__main__":
    unittest.main()
