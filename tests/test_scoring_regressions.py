import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.metrics import task_passed
from harness.web_fetch.reference_lifecycle import (
    reference_revision_cases, valid_scoring_revision,
)
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_regressions import (
    load_scoring_regressions, require_scoring_regressions,
    valid_correction_provenance,
)
from scripts.revise_body_scoring import correction_provenance


class ScoringRegressionTests(unittest.TestCase):
    def test_every_registered_false_verdict_control_runs_through_shared_scorer(self):
        rows = load_scoring_regressions()
        root_modes = {}
        for identifier, row in rows.items():
            with self.subTest(regression=identifier):
                metrics = score_attempt(
                    row["case"], row["artifact"].encode(), ok=row["ok"],
                    metadata=row.get("metadata"), title=row.get("title"),
                )
                verdict = task_passed(
                    row["ok"], metrics, row["case"].get("task_kind", "body")
                )
                self.assertEqual(verdict, row["expected_task_pass"])
                for key, expected in row.get("expected_metrics", {}).items():
                    self.assertEqual(metrics.get(key), expected, key)
                root_modes.setdefault(row["root_cause"], set()).add(row["failure_mode"])
        paired = {"answer_surface_omission", "body_scope_calibration",
                  "declared_scope_exclusions", "declared_truncation",
                  "entity_identity", "record_ownership",
                  "resource_output_scope", "resource_surface_parsing"}
        for root_cause in paired:
            self.assertEqual(root_modes.get(root_cause), {"false-positive", "false-negative"})

    def test_scoring_correction_cannot_cite_missing_or_empty_regressions(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            require_scoring_regressions([])
        with self.assertRaisesRegex(ValueError, "Unknown"):
            require_scoring_regressions(["missing"])
        with self.assertRaisesRegex(ValueError, "category"):
            require_scoring_regressions(
                ["answer-title-supplies-required-fact"], category="body-scope"
            )
        self.assertEqual(
            require_scoring_regressions([
                "answer-title-supplies-required-fact",
                "answer-title-supplies-required-fact",
            ]),
            ["answer-title-supplies-required-fact"],
        )

    def test_revision_provenance_reports_fp_fn_direction_and_fails_closed(self):
        regression = "answer-title-supplies-required-fact"
        provenance = correction_provenance(
            "answer-surface",
            "The source title was omitted from the answer-bearing scoring surface.",
            [regression],
            [
                {"attempt_id": "fp", "before": True, "after": False},
                {"attempt_id": "fn", "before": False, "after": True},
            ],
        )
        self.assertEqual(provenance["regression_ids"], [regression])
        self.assertEqual(provenance["verdict_corrections"], {
            "false_positives": 1, "false_negatives": 1, "total": 2,
        })
        with self.assertRaisesRegex(ValueError, "category"):
            correction_provenance("other", "A sufficiently concrete root cause explanation.", [regression])
        with self.assertRaisesRegex(ValueError, "concrete"):
            correction_provenance("answer-surface", "format issue", [regression])

    def test_schema_two_receipt_recomputes_provenance_instead_of_trusting_counts(self):
        decisions = [{"attempt_id": "fn", "before": False, "after": True}]
        receipt = {
            "schema_version": 2,
            "task_decision_changes": decisions,
            "correction_provenance": correction_provenance(
                "answer-surface",
                "The source title was omitted from the answer-bearing scoring surface.",
                ["answer-title-supplies-required-fact"], decisions,
            ),
        }
        self.assertTrue(valid_correction_provenance(receipt))
        receipt["correction_provenance"]["verdict_corrections"]["false_negatives"] = 0
        self.assertFalse(valid_correction_provenance(receipt))
        receipt["schema_version"] = 3
        self.assertFalse(valid_correction_provenance(receipt))

    def test_schema_two_receipt_accepts_every_field_the_revision_tool_can_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = b'{"cases": []}\n'
            results = b''
            (root / "dataset.json").write_bytes(dataset)
            (root / "results.jsonl").write_bytes(results)
            decisions = [{"attempt_id": "fn", "before": False, "after": True}]
            receipt = {
                "schema_version": 2,
                "kind": "audited_scoring_rule_revision",
                "provider_calls": 0,
                "input_dataset_sha256": hashlib.sha256(dataset).hexdigest(),
                "input_results_sha256": hashlib.sha256(results).hexdigest(),
                "output_dataset_sha256": hashlib.sha256(dataset).hexdigest(),
                "output_results_sha256": hashlib.sha256(results).hexdigest(),
                "changed_cases": [{"case_id": "case", "fields": ["forbidden_any"]}],
                "task_decision_changes": decisions,
                "correction_provenance": correction_provenance(
                    "task-predicate",
                    "A forbidden marker was not included in the shared task predicate.",
                    ["successful-empty-response-is-not-task-success"], decisions,
                ),
            }
            (root / "scoring-revisions.json").write_text(json.dumps([receipt]))
            self.assertTrue(valid_scoring_revision(root))

    def test_scoring_revision_requires_a_continuous_fully_valid_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = b'{"cases": []}\n'
            results = b''
            (root / "dataset.json").write_bytes(dataset)
            (root / "results.jsonl").write_bytes(results)
            current = (hashlib.sha256(dataset).hexdigest(),
                       hashlib.sha256(results).hexdigest())
            base = {
                "kind": "audited_scoring_rule_revision",
                "provider_calls": 0,
                "changed_cases": [],
                "task_decision_changes": [],
            }
            first = {
                **base,
                "input_dataset_sha256": "a" * 64,
                "input_results_sha256": "b" * 64,
                "output_dataset_sha256": "c" * 64,
                "output_results_sha256": "d" * 64,
            }
            final = {
                **base,
                "input_dataset_sha256": "c" * 64,
                "input_results_sha256": "d" * 64,
                "output_dataset_sha256": current[0],
                "output_results_sha256": current[1],
            }

            broken = json.loads(json.dumps([first, final]))
            broken[1]["input_results_sha256"] = "e" * 64
            (root / "scoring-revisions.json").write_text(json.dumps(broken))
            self.assertFalse(valid_scoring_revision(root))

            unauthorized = json.loads(json.dumps([first, final]))
            unauthorized[0]["changed_cases"] = [{
                "case_id": "case", "fields": ["url"],
            }]
            (root / "scoring-revisions.json").write_text(json.dumps(unauthorized))
            self.assertFalse(valid_scoring_revision(root))

            (root / "scoring-revisions.json").write_text(json.dumps([first, final]))
            self.assertTrue(valid_scoring_revision(root))

    def test_reference_revision_requires_the_same_root_cause_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = b'{"cases": []}\n'
            results = b''
            (root / "dataset.json").write_bytes(dataset)
            (root / "results.jsonl").write_bytes(results)
            receipt = {
                "schema_version": 2,
                "kind": "audited_reference_correction",
                "provider_calls": 0,
                "output_dataset_sha256": hashlib.sha256(dataset).hexdigest(),
                "output_results_sha256": hashlib.sha256(results).hexdigest(),
                "changed_cases": [{
                    "case_id": "article",
                    "fields": ["ground_truth_main_content"],
                    "old_reference_sha256": "a" * 64,
                    "new_reference_sha256": "b" * 64,
                }],
                "task_decision_changes": [],
                "correction_provenance": correction_provenance(
                    "reference-evidence",
                    "Site navigation was included in the independently scoped reference.",
                    ["reference-scope-excludes-site-chrome"],
                ),
            }
            path = root / "reference-revisions.json"
            path.write_text(json.dumps([receipt]))
            self.assertEqual(reference_revision_cases(root), {"article": {"a" * 64}})
            receipt["correction_provenance"]["regression_ids"] = ["missing"]
            path.write_text(json.dumps([receipt]))
            self.assertEqual(reference_revision_cases(root), {})


if __name__ == "__main__":
    unittest.main()
