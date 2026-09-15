import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.stabilize_mutable_body_cases import stabilize


class StabilizeMutableBodyCaseTests(unittest.TestCase):
    def test_preserves_non_body_snapshot_policy(self):
        dataset = {"cases": [{
            "id": "dynamic", "track": "web", "task_kind": "structure",
            "source_benchmark": {
                "snapshot_metric_scope": "captured_sample_diagnostic",
                "stable_annotation": {"content_kind": "changing_public_list"},
            },
        }]}
        result, changed = stabilize(dataset, Path("unused"))
        self.assertEqual(changed, [])
        self.assertEqual(
            result["cases"][0]["source_benchmark"]["snapshot_metric_scope"],
            "captured_sample_diagnostic",
        )

    def test_mutable_body_uses_stable_anchors_and_snapshot_becomes_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            sources=Path(directory);folder=sources/"mutable";folder.mkdir()
            reference=b"Stable identity\nRotating news\nStable purpose"
            (folder/"reference.txt").write_bytes(reference)
            dataset={"cases":[{"id":"mutable","track":"web","task_kind":"body",
                "ground_truth_main_content":reference.decode(),
                "required_facts":[{"id":"identity","alternatives":["Stable identity"]},
                                  {"id":"purpose","alternatives":["Stable purpose"]}],
                "forbidden_any":["Rotating news","CAPTCHA"],
                "body_requirements":{"version":3},"source_benchmark":{}}]}
            result,changed=stabilize(dataset,sources)
            case=result["cases"][0]
            self.assertEqual(changed,["mutable"])
            self.assertNotIn("body_requirements",case)
            self.assertEqual(case["acceptance_contract"]["schema"],"body_text")
            self.assertEqual(case["forbidden_any"],["CAPTCHA"])
            annotation=case["mutable_body_policy"]["stable_annotation"]
            self.assertEqual(annotation["anchor_evidence_sha256"],
                             hashlib.sha256(reference).hexdigest())

    def test_missing_anchor_fails_before_dataset_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            sources=Path(directory);folder=sources/"mutable";folder.mkdir()
            (folder/"reference.txt").write_text("Different content")
            dataset={"cases":[{"id":"mutable","track":"web","task_kind":"body",
                "ground_truth_main_content":"Old body",
                "required_facts":[{"id":"identity","alternatives":["Stable identity"]}],
                "source_benchmark":{}}]}
            with self.assertRaisesRegex(ValueError,"stable anchors absent"):
                stabilize(dataset,sources)


if __name__ == "__main__":
    unittest.main()
