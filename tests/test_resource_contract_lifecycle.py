import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.assembly import (
    ALLOWED_REVISION_FIELDS as ASSEMBLY_REVISION_FIELDS,
)
from harness.web_fetch.assembly import (
    bind_assembled_scoring_revision,
)
from harness.web_fetch.dynamic_cohort import frozen_case
from harness.web_fetch.reference_lifecycle import (
    ReferenceLifecycleError,
    check_case_scoring_revision_binding,
    scope_identity,
    sha256,
    valid_scoring_revision,
)
from harness.web_fetch.report_build import build_report
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
from scripts.rebind_result_review_metadata import (
    ALLOWED_REVISION_FIELDS as REBIND_REVISION_FIELDS,
)
from scripts.revise_body_scoring import ALLOWED_FIELDS as SCORING_REVISION_FIELDS

RESOURCE_FIELDS = {"expected_resources", "resource_output_contract", "task_prompt"}


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()


class ResourceContractLifecycleTests(unittest.TestCase):
    def resource_case(self):
        return {
            "id": "image",
            "name": "Image",
            "url": "https://example.test/gallery",
            "category": "resource",
            "track": "image",
            "task_kind": "resource",
            "task_prompt": "Return the target image.",
            "expected_any": [],
            "expected_resources": [{
                "id": "hero",
                "urls": ["/hero-large.jpg"],
                "aliases": ["/hero-480w.jpg"],
            }],
            "resource_output_contract": {
                "candidate_scope": "image_answer_fields",
                "unexpected": "reject",
            },
            "content_score_applicable": False,
        }

    def test_scope_identity_binds_resource_targets_and_output_policy(self):
        case = self.resource_case()
        identity = scope_identity(case)
        contract = identity["evaluation_contract"]
        self.assertEqual(contract["expected_resources"], case["expected_resources"])
        self.assertEqual(
            contract["resource_output_contract"], case["resource_output_contract"],
        )

        original = digest(identity)
        changed_targets = json.loads(json.dumps(case))
        changed_targets["expected_resources"][0]["urls"] = ["/different.jpg"]
        changed_policy = json.loads(json.dumps(case))
        changed_policy["resource_output_contract"]["unexpected"] = "allow"
        self.assertNotEqual(digest(scope_identity(changed_targets)), original)
        self.assertNotEqual(digest(scope_identity(changed_policy)), original)

    def test_audited_recall_policy_may_update_matching_prompt_but_not_prompt_alone(self):
        original = self.resource_case()
        original["source_verification"] = {"reference_sha256": sha256("")}
        binding = {
            "reference_sha256": sha256(""),
            "expected_normalized_sha256": sha256(""),
            "scope_identity": scope_identity(original),
        }
        corrected = json.loads(json.dumps(original))
        corrected["resource_output_contract"]["unexpected"] = "allow"
        corrected["task_prompt"] = "Return every target; report extra images as precision only."
        check_case_scoring_revision_binding(corrected, binding)

        prompt_only = json.loads(json.dumps(original))
        prompt_only["task_prompt"] = corrected["task_prompt"]
        with self.assertRaises(ReferenceLifecycleError):
            check_case_scoring_revision_binding(prompt_only, binding)

    def test_dynamic_frozen_case_retains_complete_resource_contract(self):
        case = self.resource_case()
        frozen = frozen_case(case)
        self.assertEqual(frozen["expected_resources"], case["expected_resources"])
        self.assertEqual(
            frozen["resource_output_contract"], case["resource_output_contract"],
        )

    def test_resource_fields_are_accepted_through_revision_and_assembly(self):
        self.assertTrue(RESOURCE_FIELDS <= SCORING_REVISION_FIELDS)
        self.assertTrue(RESOURCE_FIELDS <= ASSEMBLY_REVISION_FIELDS)
        self.assertTrue(RESOURCE_FIELDS <= REBIND_REVISION_FIELDS)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = {
                "comparison": {"require_independent_result_review": True},
                "cases": [self.resource_case()],
            }
            record = {
                "attempt_id": "p__image__r001",
                "provider": "p",
                "case_id": "image",
                "ok": True,
                "metrics": {"resource_pass": True},
            }
            (root / "dataset.json").write_text(json.dumps(dataset))
            (root / "results.jsonl").write_text(json.dumps(record) + "\n")
            review = {
                "schema_version": 1,
                "bindings": {
                    "dataset_sha256": hashlib.sha256(
                        (root / "dataset.json").read_bytes()
                    ).hexdigest(),
                    "results_sha256": hashlib.sha256(
                        (root / "results.jsonl").read_bytes()
                    ).hexdigest(),
                },
                "review_protocol": {
                    "machine_judgments_withheld": True,
                    "scope": "all",
                },
                "attempts": [{
                    "attempt_id": record["attempt_id"],
                    "machine_verdict": "pass",
                    "agent_verdict": "pass",
                    "final_verdict": "pass",
                }],
            }
            (root / "result-review.json").write_text(json.dumps(review))
            history = [{
                "kind": "audited_scoring_rule_revision",
                "provider_calls": 0,
                "input_dataset_sha256": "a" * 64,
                "input_results_sha256": "b" * 64,
                "output_dataset_sha256": "c" * 64,
                "output_results_sha256": "d" * 64,
                "changed_cases": [{
                    "case_id": "image",
                    "fields": sorted(RESOURCE_FIELDS),
                }],
                "task_decision_changes": [],
            }]
            (root / "scoring-revisions.json").write_text(json.dumps(history))
            self.assertTrue(bind_assembled_scoring_revision(root))
            self.assertTrue(valid_scoring_revision(root))

    def test_report_rejects_missing_or_stale_resource_contract_hash(self):
        case = self.resource_case()
        record = {
            "attempt_id": "p__image__r001",
            "provider": "p",
            "case_id": "image",
            "artifact_path": "artifacts/p/image/run.txt",
            "metrics": {
                "scoring_source_sha256": SCORING_SOURCE_SHA256,
                "content_sha256": hashlib.sha256(b"output").hexdigest(),
            },
            "ok": True,
        }
        wrong = digest({
            "expected_resources": case["expected_resources"],
            "resource_output_contract": {
                **case["resource_output_contract"], "unexpected": "allow",
            },
        })
        for actual in (None, wrong):
            with self.subTest(contract_hash=actual), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source"
                output = Path(directory) / "report"
                source.mkdir()
                current = json.loads(json.dumps(record))
                if actual is not None:
                    current["metrics"]["resource_acceptance_contract_sha256"] = actual
                (source / "dataset.json").write_text(json.dumps({"cases": [case]}))
                (source / "results.jsonl").write_text(json.dumps(current) + "\n")
                with patch("harness.web_fetch.report_build.generate") as generated:
                    with self.assertRaisesRegex(
                        ValueError, "Resource acceptance contract hash missing or mismatched",
                    ):
                        build_report(source, output)
                generated.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
