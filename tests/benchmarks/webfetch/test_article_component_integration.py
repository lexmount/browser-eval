"""Additive component acceptance must apply through every public scoring boundary."""
from .article_contract_fixtures import comment_labels
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lexbench_webfetch.benchmark import public_task
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.scoring import score_attempt


class ArticleComponentIntegration(unittest.TestCase):
    def setUp(self):
        self.comment = {**comment_labels(),
            "version": 2, "record_selector": ".comment", "author_selector": ".author",
            "body_selector": ".text", "section_markers": ["Comments"],
            "minimum_comments": 1,
        }
        self.title = {"version": 1, "alternatives": ["Life and dignity"],
                      "html_selector": "h1"}
        self.case = {"id": "article-test", "name": "Life and dignity",
                     "url": "https://example.test/article", "category": "article",
                     "expected_any": [], "task_kind": "body",
                     "acceptance_contract": {"version": 1, "schema": "body_text",
                         "minimum_records": 0, "page_patterns": ["Life and dignity"],
                         "stable_text_role": "required", "stable_text_anchors": []},
                     "source_benchmark": {"title_contract": self.title,
                                          "comment_contract": self.comment}}
        self.complete = "# Life and dignity\n\nThe author writes Life and dignity again.\n\nComments\n\n## Reader\nA thoughtful book."

    def run_score(self, content):
        return evaluate_response(self.case, content.encode())

    def test_title_body_and_comments_are_independently_required(self):
        self.assertTrue(self.run_score(self.complete)["passed"])
        self.assertFalse(self.run_score(self.complete.split("Comments")[0])["passed"])
        self.assertFalse(self.run_score(self.complete.replace("# Life and dignity\n", ""))["passed"])
        self.assertFalse(self.run_score(self.complete.replace("A thoughtful book.", ""))["passed"])
        # Original repetition is valid and remains untouched.
        self.assertIn("The author writes Life and dignity again.", self.complete)

    def test_removed_runtime_reference_arguments_are_rejected(self):
        for argument in ('comments_reference', 'reference_context'):
            with self.subTest(argument=argument), self.assertRaises(TypeError):
                evaluate_response(self.case, self.complete.encode(), **{argument: {}})

    def test_public_projection_keeps_component_contracts(self):
        projected = public_task(self.case)
        self.assertEqual(projected["source_benchmark"], self.case["source_benchmark"])

    def test_old_case_without_components_is_unchanged(self):
        original = copy.deepcopy(self.case)
        original.pop("source_benchmark")
        result = evaluate_response(original, b"Life and dignity")
        self.assertTrue(result["passed"])
        self.assertTrue(result["measured"])
        self.assertNotIn("article_comments_pass", result["metrics"])

    def test_rescore_does_not_retain_previous_component_metrics(self):
        previous = self.run_score(self.complete)["metrics"]
        original = copy.deepcopy(self.case)
        original.pop("source_benchmark")
        metrics = score_attempt(original, b"Life and dignity", ok=True,
                                previous_metrics=previous)
        self.assertNotIn("article_comments_reference_status", metrics)
        self.assertNotIn("article_title_pass", metrics)



class StaticCommentCoverageIntegration(unittest.TestCase):
    def setUp(self):
        original = ArticleComponentIntegration()
        original.setUp()
        self.case = copy.deepcopy(original.case)
        self.contract = self.case["source_benchmark"]["comment_contract"]
        self.contract.update(version=2, minimum_comments=2)
        self.complete = "# Life and dignity\n\nArticle prose.\n\nComments\n\n## New reader\nA new comment today.\n\n## Another reader\nA different response."

    def test_static_coverage_needs_no_runtime_reference(self):
        result = evaluate_response(self.case, self.complete.encode())
        self.assertTrue(result["passed"])
        self.assertTrue(result["measured"])
        self.assertNotIn("article_comments_reference_status", result["metrics"])
        self.assertEqual(result["metrics"]["content_decision_basis"],
                         "base_acceptance_and_required_components")

    def test_missing_comment_section_and_below_floor_are_measured_failures(self):
        for content in (self.complete.split("Comments")[0],
                        self.complete.split("## Another reader")[0]):
            with self.subTest(content=content):
                result = evaluate_response(self.case, content.encode())
                self.assertTrue(result["measured"])
                self.assertFalse(result["passed"])
                self.assertFalse(result["metrics"]["article_comments_pass"])

    def test_comment_names_text_and_peer_heading_level_are_not_frozen(self):
        changed = self.complete.replace("New reader", "Changed author").replace(
            "A new comment today.", "Entirely different current prose.").replace(
            "## Changed author", "### Changed author").replace(
            "## Another reader", "### Another reader")
        self.assertTrue(evaluate_response(self.case, changed.encode())["passed"])

    def test_no_comment_contract_has_no_comment_requirement(self):
        self.case["source_benchmark"].pop("comment_contract")
        result = evaluate_response(self.case, b"# Life and dignity\n\nArticle prose.")
        self.assertTrue(result["passed"])
        self.assertNotIn("article_comments_pass", result["metrics"])

    def test_cli_scores_static_comments_without_reference_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.json").write_text(json.dumps(self.case))
            (root / "answer.md").write_text(self.complete)
            result = subprocess.run([sys.executable, "-m", "lexbench_webfetch.cli", "score",
                "--task", str(root / "task.json"), "--response", str(root / "answer.md")],
                text=True, capture_output=True, check=True)
            self.assertTrue(json.loads(result.stdout)["passed"])
