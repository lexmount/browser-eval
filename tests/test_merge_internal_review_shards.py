import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch import result_review

from harness.web_fetch.result_review import merge_reviews


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
                                "machine_judgments_withheld": True,
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
            self.assertFalse(review["review_protocol"]["complete_artifact_paths_supplied"])
            self.assertEqual(review["reviewer"]["sources"],
                             [{"model": "prior-model"}, {"model": "new-model"}])

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

class ReviewInstallationSafetyTests(unittest.TestCase):
    fixture = MergeInternalReviewShardsTests.fixture
    def test_invalid_adjudication_preserves_installed_review_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            original = prior.read_bytes()
            document = json.loads(shard.read_text())
            document['attempts'][0]['agent_verdict'] = 'fail'
            shard.write_text(json.dumps(document))
            decisions = root / 'decisions.json'
            decisions.write_text(json.dumps({'passed': {'final_verdict': 'fail', 'reason': 'error'}}))
            output = root / 'full.json'
            with self.assertRaises(ValueError):
                merge_reviews(evidence, [shard], output, prior_review=prior,
                              adjudications=decisions, allow_confirmed_errors=True, install=True)
            self.assertEqual(prior.read_bytes(), original)
            self.assertFalse(output.exists())

    def test_output_cannot_overwrite_evidence_or_shards(self):
        for destination in ('dataset.json', 'results.jsonl', 'shard'):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence, prior, shard = self.fixture(root)
                output = shard if destination == 'shard' else evidence / destination
                original = output.read_bytes()
                with self.assertRaises(ValueError):
                    merge_reviews(evidence, [shard], output, prior_review=prior)
                self.assertEqual(output.read_bytes(), original)

    def test_linked_shard_cannot_supply_review_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            linked = root / 'linked.json'
            linked.symlink_to(shard)
            with self.assertRaises(ValueError):
                merge_reviews(evidence, [linked], root / 'full.json', prior_review=prior)

    def test_unblinded_review_cannot_be_certified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            original = prior.read_bytes()
            document = json.loads(shard.read_text())
            document['review_protocol'].pop('machine_judgments_withheld')
            shard.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, 'not scorer-independent'):
                merge_reviews(evidence, [shard], root / 'full.json', prior_review=prior, install=True)
            self.assertEqual(prior.read_bytes(), original)

    def test_changed_input_during_staging_preserves_installed_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            original = prior.read_bytes()
            output = root / 'full.json'
            write = result_review.write_review_document

            def change_input(target, review, *, before_replace=None):
                shard.write_text('{}')
                return write(target, review, before_replace=before_replace)

            with patch.object(result_review, 'write_review_document', side_effect=change_input):
                with self.assertRaisesRegex(ValueError, 'input changed'):
                    merge_reviews(evidence, [shard], output, prior_review=prior, install=True)
            self.assertEqual(prior.read_bytes(), original)
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob('.result-review-*')), [])

    def test_final_release_validator_runs_before_any_output_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            original = prior.read_bytes()
            validator = result_review.require_result_review

            def reject_complete(source, dataset, records, review_document=None):
                if review_document is not None:
                    raise ValueError('complete review rejected by release gate')
                return validator(source, dataset, records)

            with patch.object(result_review, 'require_result_review', side_effect=reject_complete):
                with self.assertRaisesRegex(ValueError, 'release gate'):
                    merge_reviews(evidence, [shard], root / 'full.json', prior_review=prior, install=True)
            self.assertEqual(prior.read_bytes(), original)
            self.assertFalse((root / 'full.json').exists())

    def test_install_can_use_canonical_output_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, shard = self.fixture(root)
            merged = merge_reviews(evidence, [shard], prior, prior_review=prior, install=True)
            self.assertEqual(json.loads(prior.read_text()), merged)


if __name__ == '__main__':
    unittest.main()
