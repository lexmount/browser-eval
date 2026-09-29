import json
from pathlib import Path
import unittest
import tempfile

from scripts.correct_webmainbench_scoring import (
    CONTROL_IDS, REGISTRY, json_digest, revised_metrics, validate_population_binding,
)


class FakeResult:
    def __init__(self, score=0.0, success=True, availability="reference_not_applicable"):
        self.score = score
        self.success = success
        self.details = ({"availability": availability}
                        if not success and availability else {})
        self.error_message = "unavailable" if not success else None


class FakeMetric:
    def __init__(self, available=True, score=0.5,
                 unavailable_kind="reference_not_applicable"):
        self.available = available
        self.score = score
        self.unavailable_kind = unavailable_kind

    def calculate(self, *args, **kwargs):
        score = 0.0 if args and args[0] == "" else self.score
        return FakeResult(score, self.available, self.unavailable_kind)

    def _extract_formula_content(self, *args):
        return "x" if self.available else ""

    def _extract_table_content(self, *args):
        return "table" if self.available else ""


class FakeScorer:
    def __init__(self):
        self.metrics = {
            "text_edit": FakeMetric(score=0.5),
            "code_edit": FakeMetric(score=0.25),
            "formula_edit": FakeMetric(available=False, score=0.75),
            "table_edit": FakeMetric(available=True),
            "table_TEDS": FakeMetric(available=True, score=0.4),
        }

    def calculate_all(self, *, predicted_content, groundtruth_content,
                      groundtruth_feature_scope, **kwargs):
        del groundtruth_content, kwargs
        available = {
            "text_edit": True,
            "code_edit": bool(groundtruth_feature_scope.get("code")),
            "formula_edit": bool(groundtruth_feature_scope.get("equation")),
            "table_edit": bool(groundtruth_feature_scope.get("table", ["data"])),
            "table_TEDS": bool(groundtruth_feature_scope.get("table", ["data"])),
        }
        scores = {"text_edit": 0.5, "code_edit": 0.25,
                  "formula_edit": 0.75, "table_edit": 0.5, "table_TEDS": 0.4}
        if not predicted_content:
            scores = {name: 0.0 for name in scores}
        results = {name: FakeResult(scores[name], is_available)
                   for name, is_available in available.items()}
        applicable = {name: result.score for name, result in results.items() if result.success}
        results["overall"] = FakeResult(sum(applicable.values()) / len(applicable), True)
        return results


class WebMainBenchScoringCorrectionTests(unittest.TestCase):
    def test_failure_is_zero_inside_reference_population(self):
        old = {
            "table_edit": {"score": 0.8, "success": True, "details": {}},
            "table_TEDS": {"score": 0.7, "success": True, "details": {}},
        }
        result = revised_metrics(old, FakeScorer(), "", "reference",
                                 {"code": ["inline"], "table": ["data"], "equation": []}, False)
        self.assertEqual(result["text_edit"]["score"], 0.0)
        self.assertEqual(result["table_edit"]["score"], 0.0)
        self.assertEqual(result["table_TEDS"]["score"], 0.0)
        self.assertFalse(result["formula_edit"]["success"])

    def test_success_recomputes_all_metrics_with_bound_scorer(self):
        old = {name: {"score": 0.99, "success": True, "details": {}}
               for name in ("text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS")}
        scorer = FakeScorer()
        scorer.metrics["formula_edit"].available = True
        result = revised_metrics(old, scorer, "content", "reference",
                                 {"code": ["inline"], "table": ["data"], "equation": ["inline"]},
                                 True, reference_changed=True)
        self.assertEqual(result["text_edit"]["score"], 0.5)
        self.assertEqual(result["code_edit"]["score"], 0.25)
        self.assertEqual(result["formula_edit"]["score"], 0.75)
        self.assertEqual(result["table_edit"]["score"], 0.5)
        self.assertEqual(result["table_TEDS"]["score"], 0.4)

    def test_applicable_metric_error_remains_in_shared_population_as_zero(self):
        scorer = FakeScorer()
        scorer.metrics["table_TEDS"].available = False
        scorer.metrics["table_TEDS"].unavailable_kind = None
        result = revised_metrics({}, scorer, "content", "reference",
                                 {"code": [], "table": ["data"], "equation": []}, True)
        self.assertTrue(result["table_TEDS"]["success"])
        self.assertEqual(result["table_TEDS"]["score"], 0.0)
        self.assertEqual(result["table_TEDS"]["details"]["prediction_source"], "metric_error")

    def test_track_controls_are_paired_by_root_cause(self):
        document = json.loads(Path(REGISTRY).read_text())
        self.assertEqual({row["id"] for row in document["cases"]}, set(CONTROL_IDS))
        modes = {}
        for row in document["cases"]:
            modes.setdefault(row["root_cause"], set()).add(row["failure_mode"])
        self.assertTrue(modes)
        self.assertTrue(all(value == {"false-positive", "false-negative"}
                            for value in modes.values()))

    def test_population_binding_accepts_a_fully_applied_corrected_dataset(self):
        row = {"track_id": "case", "url": "https://example.test", "html": "<p>x</p>",
               "groundtruth_content": "corrected", "meta": {"code": []}}
        binding = {
            "row_count": 1,
            "ordered_track_ids_sha256": json_digest(["case"]),
            "rows_sha256": json_digest([row]),
        }
        manifest = {"schema_version": 2, "input_population": {**binding, "rows_sha256": "old"},
                    "output_population": binding}
        dataset = {"cases": [{"id": "case", "meta": row["meta"]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "references").mkdir()
            (root / "references/case.md").write_text("corrected")
            validate_population_binding(root, dataset, [row], manifest)


if __name__ == "__main__":
    unittest.main()
