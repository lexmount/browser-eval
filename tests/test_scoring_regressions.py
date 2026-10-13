import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.metrics import task_passed, task_measured
from harness.web_fetch.reference_lifecycle import (
    reference_revision_cases,
    scoring_revision_history_sha256,
    valid_scoring_revision,
)
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_regressions import (
    active_scoring_regressions, require_scoring_regressions,
    valid_correction_provenance,
)
from harness.web_fetch.scoring_revision import correction_provenance


class ScoringRegressionTests(unittest.TestCase):
    def test_policy_supersession_preserves_inputs_and_rejects_changed_body(self):
        document=json.loads((Path(__file__).resolve().parents[1]/'evaluation/scoring-regressions.json').read_text())
        self.assertTrue(document['superseded_cases'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'controls.json'
            path.write_text(json.dumps(document))
            current=active_scoring_regressions(path)
            old,new=next(iter(document['superseded_cases'].items()))
            self.assertNotIn(old,current)
            self.assertIn(new,current)
            for field,value in (('artifact','Changed body'),('final_url','https://different.test/')):
                changed=json.loads(json.dumps(document))
                next(row for row in changed['cases'] if row['id']==new)[field]=value
                path.write_text(json.dumps(changed))
                with self.subTest(field=field),self.assertRaises(ValueError):
                    active_scoring_regressions(path)
            changed=json.loads(json.dumps(document))
            changed['superseded_cases'][old]='nonexistent-control'
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                active_scoring_regressions(path)

    def test_report_prose_rebinding_preserves_scoring_history_and_rejects_task_edits(self):
        from harness.web_fetch.scoring_revision import rebind_report_interpretation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'{"cases": [{"id": "a", "url": "https://example.test"}]}\n'
            (root/'dataset.json').write_bytes(raw)
            (root/'results.jsonl').write_bytes(b'')
            receipt = {'kind': 'audited_scoring_rule_revision', 'provider_calls': 0,
                       'changed_cases': [], 'task_decision_changes': [],
                       'input_dataset_sha256': 'a'*64, 'input_results_sha256': 'b'*64,
                       'output_dataset_sha256': hashlib.sha256(raw).hexdigest(),
                       'output_results_sha256': hashlib.sha256(b'').hexdigest()}
            history = json.dumps([receipt]).encode()
            (root/'scoring-revisions.json').write_bytes(history)
            (root/'article.json').write_bytes(b'{}')
            rebind_report_interpretation(root, {'path': 'article.json',
                'sha256': hashlib.sha256(b'{}').hexdigest()})
            self.assertTrue(valid_scoring_revision(root))
            self.assertEqual((root/'scoring-revisions.json').read_bytes(), history)
            self.assertEqual((root/'results.jsonl').read_bytes(), b'')
            dataset = json.loads((root/'dataset.json').read_text())
            dataset['cases'][0]['url'] = 'https://different.test'
            changed = json.dumps(dataset).encode()
            revisions = json.loads((root/'report-binding-revisions.json').read_text())
            revisions[0]['output_dataset_sha256'] = hashlib.sha256(changed).hexdigest()
            (root/revisions[0]['output_dataset_path']).write_bytes(changed)
            (root/'dataset.json').write_bytes(changed)
            (root/'report-binding-revisions.json').write_text(json.dumps(revisions))
            self.assertFalse(valid_scoring_revision(root))

    def test_historical_blind_review_keeps_revision_chain_readable_after_new_anomaly_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            artifact=b''
            (root/'a.txt').write_bytes(artifact)
            dataset={'comparison':{'require_independent_result_review':True,
                                   'require_anomaly_review':True},
                     'cases':[{'id':'a','track':'web','task_kind':'body'}]}
            record={'attempt_id':'p__a','provider':'p','case_id':'a','ok':True,
                    'artifact_path':'a.txt','metrics':{
                        'content_sha256':hashlib.sha256(artifact).hexdigest(),
                        'content_pass':False}}
            dataset_raw=(json.dumps(dataset)+'\n').encode()
            results_raw=(json.dumps(record)+'\n').encode()
            (root/'dataset.json').write_bytes(dataset_raw)
            (root/'results.jsonl').write_bytes(results_raw)
            review={'schema_version':1,'bindings':{
                        'dataset_sha256':hashlib.sha256(dataset_raw).hexdigest(),
                        'results_sha256':hashlib.sha256(results_raw).hexdigest()},
                    'review_protocol':{'scope':'all','machine_judgments_withheld':True},
                    'attempts':[{'attempt_id':'p__a','machine_verdict':'fail',
                                 'agent_verdict':'fail','agent_evidence':'saved output is empty',
                                 'final_verdict':'fail'}]}
            review_raw=(json.dumps(review)+'\n').encode()
            history_dir=root/'review-history';history_dir.mkdir()
            (history_dir/'result-review-old.json').write_bytes(review_raw)
            identity=(hashlib.sha256(dataset_raw).hexdigest(),
                      hashlib.sha256(results_raw).hexdigest())
            receipt={'kind':'audited_assembled_scoring_revision','provider_calls':0,
                     'input_dataset_sha256':identity[0],'input_results_sha256':identity[1],
                     'output_dataset_sha256':identity[0],'output_results_sha256':identity[1],
                     'changed_cases':[],'task_decision_changes':[],
                     'source_scoring_receipts_sha256':scoring_revision_history_sha256([]),
                     'independent_result_review_sha256':hashlib.sha256(review_raw).hexdigest()}
            (root/'scoring-revisions.json').write_text(json.dumps([receipt]))
            self.assertTrue(valid_scoring_revision(root))

    def test_every_registered_false_verdict_control_runs_through_shared_scorer(self):
        rows = active_scoring_regressions()
        root_modes = {}
        for identifier, row in rows.items():
            with self.subTest(regression=identifier):
                metrics = score_attempt(
                    row["case"], row["artifact"].encode(), ok=row["ok"],
                    metadata=row.get("metadata"), title=row.get("title"),
                    final_url=row.get("final_url"),
                )
                verdict = task_passed(
                    row["ok"], metrics, row["case"].get("task_kind", "body")
                )
                self.assertEqual(verdict, row["expected_task_pass"])
                if "expected_task_measured" in row:
                    self.assertEqual(task_measured(metrics), row["expected_task_measured"])
                for key, expected in row.get("expected_metrics", {}).items():
                    self.assertEqual(metrics.get(key), expected, key)
                root_modes.setdefault(row["root_cause"], set()).add(row["failure_mode"])
        paired = {"answer_surface_omission", "body_scope_calibration",
                  "declared_scope_exclusions", "declared_truncation",
                  "entity_identity", "record_ownership",
                  "resource_output_scope", "resource_surface_parsing",
                  "semantic_requirement_location", "supplemental_content_scope",
                  "blocked_page_detection", "completion_contract",
                  "layout_sensitive_reference_fact", "named_link_label_validity",
                  "semantic_inline_boundaries", "markdown_relative_autolink_visibility",
                  "stable_contract_body_conjunction", "rule_unresolved_exclusion",
                  "body_region_structure_priority"}
        paired.add("source_observed_record_threshold")
        paired.add("dynamic_page_identity_alignment")
        paired.add("dynamic_page_query_identity")
        paired.add("declared_document_language_vs_visible_language")
        paired.add("frozen_source_name_link_pair_membership")
        paired.add("markdown_table_separator_layout")
        paired.add("wikimedia_thumbnail_host_equivalence")
        paired.add("source_owned_stable_text_scope")
        paired.add("declared_full_page_stable_text_scope")
        paired.add("inline_emphasis_in_structured_fields")
        paired.add("markdown_list_prefix_ownership")
        paired.add("scoped_markdown_bookmark_format_detection")
        paired.add("scoped_markdown_list_boundary_semantics")
        paired.add("mixed_html_prose_projection")
        paired.add("pattern_output_and_prose_scope")
        paired.add("rendered_result_section_ownership")
        paired.add("equivalent_visible_label_alternatives")
        paired.add("independent_stable_anchor_order")
        paired.add("task_relevant_visible_text_scope")
        paired.add("forbidden_phrase_substring_collision")
        paired.add("scoped_markdown_caption_ownership")
        paired.update({"table_header_visual_line", "table_adjacent_split_body",
                       "composite_html_table_ownership"})
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

    def test_multi_root_revision_binds_every_cited_category(self):
        provenance = correction_provenance(
            "multi-root",
            "One offline rescore applies independently reviewed acceptance and task-predicate corrections together.",
            [
                "complete-declared-record-passes-contract",
                "known-challenge-headings-block-complete-records",
            ],
        )
        self.assertEqual(
            provenance["root_cause_categories"],
            ["acceptance-contract", "task-predicate"],
        )
        receipt = {
            "schema_version": 2,
            "task_decision_changes": [],
            "correction_provenance": provenance,
        }
        self.assertTrue(valid_correction_provenance(receipt))
        with self.assertRaisesRegex(ValueError, "multiple categories"):
            correction_provenance(
                "multi-root",
                "A claimed combined correction cites controls from only one root-cause category.",
                ["complete-declared-record-passes-contract"],
            )

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

    def test_scoring_history_crosses_explicit_reference_revision_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = b'{"cases": []}\n'
            results = b''
            (root / "dataset.json").write_bytes(dataset)
            (root / "results.jsonl").write_bytes(results)
            current = (hashlib.sha256(dataset).hexdigest(),
                       hashlib.sha256(results).hexdigest())
            base = {
                "kind": "audited_scoring_rule_revision", "provider_calls": 0,
                "changed_cases": [], "task_decision_changes": [],
            }
            first = {**base,
                "input_dataset_sha256": "a" * 64, "input_results_sha256": "b" * 64,
                "output_dataset_sha256": "c" * 64, "output_results_sha256": "d" * 64}
            final = {**base,
                "input_dataset_sha256": "e" * 64, "input_results_sha256": "f" * 64,
                "output_dataset_sha256": current[0], "output_results_sha256": current[1]}
            (root / "scoring-revisions.json").write_text(json.dumps([first, final]))
            reference = {
                "kind": "audited_reference_correction", "provider_calls": 0,
                "output_dataset_sha256": current[0], "output_results_sha256": current[1],
                "changed_cases": [], "task_decision_changes": [],
                "revision_transitions": [{
                    "input_dataset_sha256": "c" * 64, "input_results_sha256": "d" * 64,
                    "output_dataset_sha256": "e" * 64, "output_results_sha256": "f" * 64,
                }],
            }
            (root / "reference-revisions.json").write_text(json.dumps([reference]))
            self.assertTrue(valid_scoring_revision(root))
            reference["revision_transitions"][0]["output_results_sha256"] = "0" * 64
            (root / "reference-revisions.json").write_text(json.dumps([reference]))
            self.assertFalse(valid_scoring_revision(root))

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
