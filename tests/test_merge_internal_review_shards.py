import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.merge_internal_review_shards import merge_reviews


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MergeInternalReviewShardsTests(unittest.TestCase):
    def fixture(self, root):
        evidence = root / "evidence"
        evidence.mkdir()
        dataset = {
            "comparison": {"require_independent_result_review": True},
            "cases": [{"id": "case", "task_kind": "body"}],
        }
        records = [
            {"attempt_id": "failed", "provider": "a", "case_id": "case",
             "ok": True, "metrics": {"content_pass": False}},
            {"attempt_id": "passed", "provider": "b", "case_id": "case",
             "ok": True, "metrics": {"content_pass": True}},
        ]
        (evidence / "dataset.json").write_text(json.dumps(dataset))
        (evidence / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records))
        bindings = {
            "dataset_sha256": digest(evidence / "dataset.json"),
            "results_sha256": digest(evidence / "results.jsonl"),
        }
        prior = {
            "schema_version": 1, "bindings": bindings,
            "reviewer": {"model": "prior-model"},
            "review_protocol": {"machine_judgments_withheld": True,
                                "production_scorer_forbidden": True,
                                "direct_artifact_evidence_required": True,
                                "scope": "machine-failures"},
            "attempts": [{"attempt_id": "failed", "machine_verdict": "fail",
                          "agent_verdict": "fail", "agent_evidence": "missing",
                          "final_verdict": "fail", "adjudication": None}],
        }
        prior_path = evidence / "result-review.json"
        prior_path.write_text(json.dumps(prior))
        shard = {
            "schema_version": 1, "bindings": bindings,
            "reviewer": {"model": "new-model"},
            "review_protocol": {"scope": "machine-passes", "provider_filter": ["b"],
                                "production_scorer_forbidden": True,
                                "direct_artifact_evidence_required": True},
            "attempts": [{"attempt_id": "passed", "agent_verdict": "pass",
                          "agent_evidence": "required text present"}],
        }
        shard_path = root / "shard.json"
        shard_path.write_text(json.dumps(shard))
        return evidence, prior_path, shard_path

    def test_prior_failure_review_and_blinded_pass_shard_form_full_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            output = root / "full.json"
            review = merge_reviews(
                evidence, [shard], output, prior_review=prior, install=True)
            self.assertEqual(review["review_protocol"]["scope"], "all")
            self.assertEqual(review["reviewed_attempts"], 2)
            self.assertEqual(review["accepted_unreviewed_passes"], 0)
            self.assertEqual(review["disagreements"], 0)
            self.assertEqual(review["reviewer"]["model"], "new-model, prior-model")
            self.assertTrue(review["review_protocol"]["production_scorer_forbidden"])
            self.assertTrue(review["review_protocol"]["direct_artifact_evidence_required"])

    def test_merge_rejects_review_that_can_share_the_production_scorer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            document = json.loads(shard.read_text())
            document["review_protocol"].pop("production_scorer_forbidden")
            shard.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "not scorer-independent"):
                merge_reviews(evidence, [shard], root / "full.json", prior_review=prior)

    def test_pass_disagreement_cannot_be_installed_without_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            document = json.loads(shard.read_text())
            document["attempts"][0]["agent_verdict"] = "fail"
            shard.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "requires adjudication"):
                merge_reviews(evidence, [shard], root / "full.json", prior_review=prior)
            adjudications = root / "adjudications.json"
            adjudications.write_text(json.dumps({
                "passed": {"final_verdict": "fail", "reason": "confirmed false positive"}
            }))
            with self.assertRaisesRegex(ValueError, "requires correction and re-review"):
                merge_reviews(
                    evidence, [shard], root / "full.json", prior_review=prior,
                    adjudications=adjudications)
            staged = merge_reviews(
                evidence, [shard], root / "staged.json", prior_review=prior,
                adjudications=adjudications, allow_confirmed_errors=True)
            self.assertEqual(staged["confirmed_scoring_errors"], 1)
            self.assertEqual(staged["attempts"][1]["final_verdict"], "fail")
            with self.assertRaisesRegex(ValueError, "Cannot install"):
                merge_reviews(
                    evidence, [shard], root / "staged.json", prior_review=prior,
                    adjudications=adjudications, allow_confirmed_errors=True,
                    install=True)

    def test_pass_review_requires_observable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            document = json.loads(shard.read_text())
            document["attempts"][0]["agent_evidence"] = "  "
            shard.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "Review evidence is empty"):
                merge_reviews(evidence, [shard], root / "full.json", prior_review=prior)


if __name__ == "__main__":
    unittest.main()
