import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.report_build import validate_webmainbench_anomaly_review


class WebMainBenchAnomalyReviewTests(unittest.TestCase):
    def fixture(self):
        root = Path(tempfile.mkdtemp())
        for name, body in {
            "dataset.json": b"dataset\n",
            "results.jsonl": b"results\n",
            "score-summaries.json": b"scores\n",
        }.items():
            (root / name).write_bytes(body)
        attempts = [f"p{i}__case-a__r001" for i in range(30)]
        receipt = {
            "kind": "webmainbench_anomaly_root_cause_review",
            "bindings": {
                "source_dataset_sha256": "source",
                "sample_manifest_sha256": "sample",
                "dataset_json_sha256": hashlib.sha256((root / "dataset.json").read_bytes()).hexdigest(),
                "results_jsonl_sha256": hashlib.sha256((root / "results.jsonl").read_bytes()).hexdigest(),
                "score_summaries_json_sha256": hashlib.sha256((root / "score-summaries.json").read_bytes()).hexdigest(),
            },
            "scope": {
                "logical_results": 30,
                "signal_triggered_results": 30,
                "distinct_cases": 1,
                "unresolved_triggered_results": 0,
            },
            "triggered_attempt_ids": attempts,
            "cases": [{
                "case_id": "case-a",
                "affected_providers": ["p0"],
                "classification": "fixture",
                "observed_zh": "观察",
                "observed_en": "Observed",
                "cause_zh": "原因",
                "cause_en": "Cause",
            }],
            "disposition": {
                "score_bearing_results_changed": False,
                "provider_calls_replaced": False,
                "references_changed": False,
            },
        }
        dataset = {"source_dataset_sha256": "source", "sample_manifest_sha256": "sample"}
        records = [{"attempt_id": attempt, "provider": attempt.split("__", 1)[0]}
                   for attempt in attempts]
        return root, receipt, dataset, records

    def test_accepts_exactly_bound_complete_review(self):
        root, receipt, dataset, records = self.fixture()
        validate_webmainbench_anomaly_review(receipt, root, dataset, records)

    def test_retest_claim_requires_matching_private_evidence(self):
        root, receipt, dataset, records = self.fixture()
        receipt['diagnostic_evidence'] = {'moli_origin_retest_summary_sha256': hashlib.sha256(b'retest').hexdigest()}
        with self.assertRaisesRegex(ValueError, 'origin-failure retest'):
            validate_webmainbench_anomaly_review(receipt, root, dataset, records)
        path = root / 'latency-diagnostic/failure-retest/summary.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'retest')
        validate_webmainbench_anomaly_review(receipt, root, dataset, records)

    def test_rejects_changed_results_file(self):
        root, receipt, dataset, records = self.fixture()
        (root / "results.jsonl").write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "results_jsonl_sha256 mismatch"):
            validate_webmainbench_anomaly_review(receipt, root, dataset, records)

    def test_rejects_unresolved_or_result_changing_review(self):
        root, receipt, dataset, records = self.fixture()
        unresolved = copy.deepcopy(receipt)
        unresolved["scope"]["unresolved_triggered_results"] = 1
        with self.assertRaisesRegex(ValueError, "scope mismatch"):
            validate_webmainbench_anomaly_review(unresolved, root, dataset, records)
        mutating = copy.deepcopy(receipt)
        mutating["disposition"]["provider_calls_replaced"] = True
        with self.assertRaisesRegex(ValueError, "must not mutate"):
            validate_webmainbench_anomaly_review(mutating, root, dataset, records)

    def test_rejects_unknown_or_duplicate_affected_provider(self):
        root, receipt, dataset, records = self.fixture()
        for affected in (["unknown"], ["p0", "p0"], []):
            changed = copy.deepcopy(receipt)
            changed["cases"][0]["affected_providers"] = affected
            with self.subTest(affected=affected), self.assertRaisesRegex(ValueError, "scope mismatch"):
                validate_webmainbench_anomaly_review(changed, root, dataset, records)


if __name__ == "__main__":
    unittest.main()
