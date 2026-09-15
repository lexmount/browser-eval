import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.reference_lifecycle import valid_scoring_revision
from harness.web_fetch.result_review import require_result_review
from scripts.merge_result_review_revision import merge_replacement
from scripts.revise_saved_request_status import classify, extract, finalize, make_subset


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SavedRequestStatusRevisionTests(unittest.TestCase):
    def fixture(self, root: Path) -> Path:
        base = root / "base"
        base.mkdir()
        cases = [
            {"id": "denied", "task_kind": "body", "forbidden_any": ["Access Denied"]},
            {"id": "good", "task_kind": "body"},
        ]
        dataset = {"comparison": {"require_independent_result_review": True},
                   "cases": cases}
        rows = []
        for case_id, status, content, passed in (
                ("denied", 403, "Access Denied", False),
                ("good", 200, "Expected body", True)):
            artifact = f"Fetched https://example.test/{case_id} (HTTP {status})\n\n{content}"
            relative = f"artifacts/deepseek-harness-built-in/{case_id}/run-001.txt"
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(artifact)
            rows.append({
                "attempt_id": f"deepseek-harness-built-in__{case_id}__r001",
                "provider": "deepseek-harness-built-in", "case_id": case_id,
                "artifact_path": relative, "ok": True, "status_code": None,
                "error_type": None, "error_message": None,
                "metrics": {"task_kind": "body", "content_pass": passed,
                            "content_sha256": sha(artifact.encode())},
                "provider_metadata": {},
                "failure_category": "返回非目标内容" if not passed else "—",
            })
        dataset_bytes = (json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode()
        results_bytes = "".join(json.dumps(row) + "\n" for row in rows).encode()
        (base / "dataset.json").write_bytes(dataset_bytes)
        (base / "results.jsonl").write_bytes(results_bytes)
        review = {
            "schema_version": 1,
            "bindings": {"dataset_sha256": sha(dataset_bytes),
                         "results_sha256": sha(results_bytes)},
            "reviewer": {"agent": "fixture"},
            "review_protocol": {"machine_judgments_withheld": True, "scope": "all"},
            "attempts": [
                {"attempt_id": rows[0]["attempt_id"], "machine_verdict": "fail",
                 "agent_verdict": "fail", "final_verdict": "fail"},
                {"attempt_id": rows[1]["attempt_id"], "machine_verdict": "pass",
                 "agent_verdict": "pass", "final_verdict": "pass"},
            ],
        }
        (base / "result-review.json").write_text(json.dumps(review))
        (base / "scoring-revisions.json").write_text(json.dumps([{
            "kind": "audited_scoring_rule_revision", "provider_calls": 0,
            "input_dataset_sha256": "1" * 64, "input_results_sha256": "2" * 64,
            "output_dataset_sha256": sha(dataset_bytes),
            "output_results_sha256": sha(results_bytes),
            "changed_cases": [], "task_decision_changes": [],
        }]))
        return base

    def test_two_stage_correction_reuses_only_review_invariant_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self.fixture(root)
            status_only, corrected, subset = root / "status", root / "corrected", root / "subset"
            extract(base, status_only, expected_headers=2)
            status_rows = [json.loads(line) for line in
                           (status_only / "results.jsonl").read_text().splitlines()]
            self.assertEqual([row["status_code"] for row in status_rows], [403, 200])
            self.assertTrue(valid_scoring_revision(status_only))
            self.assertEqual(require_result_review(
                status_only, json.loads((status_only / "dataset.json").read_text()),
                status_rows)["reviewed_attempts"], 2)

            classify(status_only, corrected, expected_non_2xx=1)
            self.assertFalse((corrected / "result-review.json").exists())
            corrected_rows = [json.loads(line) for line in
                              (corrected / "results.jsonl").read_text().splitlines()]
            self.assertFalse(corrected_rows[0]["ok"])
            self.assertEqual(corrected_rows[0]["error_type"], "http_status")
            self.assertEqual(corrected_rows[0]["failure_category"], "源站拒绝该次访问")
            self.assertTrue(corrected_rows[1]["ok"])

            make_subset(corrected, subset)
            subset_dataset = (subset / "dataset.json").read_bytes()
            subset_results = (subset / "results.jsonl").read_bytes()
            supplemental = {
                "schema_version": 1,
                "bindings": {"dataset_sha256": sha(subset_dataset),
                             "results_sha256": sha(subset_results)},
                "reviewer": {"agent": "fixture"},
                "review_protocol": {"machine_judgments_withheld": True, "scope": "all"},
                "attempts": [{"attempt_id": corrected_rows[0]["attempt_id"],
                              "machine_verdict": "fail", "agent_verdict": "fail",
                              "final_verdict": "fail"}],
            }
            (subset / "result-review.json").write_text(json.dumps(supplemental))
            merge_replacement(corrected, status_only, subset)
            merged = json.loads((corrected / "result-review.json").read_text())
            self.assertEqual(
                merged["review_protocol"]["matrix_refresh_review"]["retained_attempts"], 1)
            self.assertEqual(
                merged["review_protocol"]["matrix_refresh_review"]["reviewed_attempts"], 1)
            finalize(status_only, corrected)
            self.assertTrue(valid_scoring_revision(corrected))
            validated = require_result_review(
                corrected, json.loads((corrected / "dataset.json").read_text()), corrected_rows)
            self.assertEqual(validated["reviewed_attempts"], 2)
            self.assertEqual(validated["accepted_unreviewed_passes"], 0)


if __name__ == "__main__":
    unittest.main()
