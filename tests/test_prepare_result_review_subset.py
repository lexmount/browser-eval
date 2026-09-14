import json
from pathlib import Path
import tempfile
import unittest

from scripts.prepare_result_review_subset import refresh_review_attempt_ids


class RefreshReviewSubsetTests(unittest.TestCase):
    def test_selects_all_methods_for_changed_task_and_only_changed_rerun(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prior, current = root / "prior", root / "current"
            prior.mkdir(); current.mkdir()
            old_cases = [{"id": "a", "rule": 1}, {"id": "b", "rule": 1}]
            new_cases = [{"id": "a", "rule": 2}, {"id": "b", "rule": 1}]
            old_rows = [
                {"attempt_id": f"{provider}-{case}", "provider": provider,
                 "case_id": case, "ok": True, "error_type": None,
                 "artifact_path": f"artifacts/{provider}-{case}.txt",
                 "provider_metadata": {},
                 "metrics": {"content_pass": True, "content_sha256": "old"}}
                for case in ("a", "b") for provider in ("p", "q")
            ]
            new_rows = json.loads(json.dumps(old_rows))
            next(row for row in new_rows if row["attempt_id"] == "p-b")["metrics"]["content_sha256"] = "new"
            for path, cases, rows in ((prior, old_cases, old_rows), (current, new_cases, new_rows)):
                (path / "dataset.json").write_text(json.dumps({
                    "cases": cases, "comparison": {"require_independent_result_review": True},
                }))
                (path / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            # This helper requires the prior review to be valid; create the
            # minimal exact full review accepted by the shared validator.
            review = {
                "schema_version": 1,
                "bindings": {},
                "review_protocol": {"scope": "all", "machine_judgments_withheld": True},
                "attempts": [],
            }
            import hashlib
            review["bindings"] = {
                "dataset_sha256": hashlib.sha256((prior / "dataset.json").read_bytes()).hexdigest(),
                "results_sha256": hashlib.sha256((prior / "results.jsonl").read_bytes()).hexdigest(),
            }
            # The production validator needs scoring fields; exercising it in
            # this synthetic unit would duplicate result-review fixtures.
            from unittest.mock import patch
            judgments = [{"attempt_id": row["attempt_id"], "agent_verdict": "pass"}
                         for row in old_rows]
            # A retained blind-review disagreement is not new review work when
            # its task and reviewer-visible evidence are unchanged.
            next(row for row in judgments if row["attempt_id"] == "q-b")["agent_verdict"] = "fail"
            with patch("scripts.prepare_result_review_subset.require_result_review",
                       return_value={"attempts": judgments}):
                self.assertEqual(
                    refresh_review_attempt_ids(current, prior),
                    {"p-a", "q-a", "p-b"},
                )


if __name__ == "__main__":
    unittest.main()
