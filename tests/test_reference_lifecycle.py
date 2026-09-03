"""Synthetic clocks and captures only: no network, browsers or real providers."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.reference_lifecycle import (
    ReferenceLifecycleError, check_case_reference_fresh, finalize_reference_checks,
    preflight_reference_checks, require_verified_reference_checks, sha256,
)
from harness.web_fetch.runner import rescore_evaluation, run_evaluation
from scripts import run_local_dynamic_builtins as local


class ReferenceLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        self.case = EvalCase(id="body", name="Body", url="https://example.test/body",
            category="static", expected_any=["Price"], ground_truth_main_content="Price 10 dollars",
            source_verification={"state": "valid", "method": "independent source capture",
                "checked_at": (self.now - timedelta(minutes=1)).isoformat(),
                "reference_sha256": sha256("Price 10 dollars"), "reference_scope": "Complete article"})
        self.dataset_path = self.root / "dataset.json"
        self.run = self.root / "run"

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def dataset(self, case=None):
        case = case or self.case
        payload = {"tool": "web_fetch", "id": "test", "cases": [deepcopy(case.__dict__)]}
        self.write_json(self.dataset_path, payload)
        return payload

    def checks(self, case=None):
        return preflight_reference_checks([case or self.case], self.dataset_path, now=self.now)

    def prepare_run(self):
        dataset = self.dataset()
        self.run.mkdir()
        self.write_json(self.run / "dataset.json", dataset)
        manifest = {"started_at": self.now.isoformat(),
            "completed_at": (self.now + timedelta(seconds=2)).isoformat(),
            "attempt_count": 1, "attempt_ids": ["p__body__r001"],
            "providers": [{"name": "p", "type": "synthetic"}],
            "dataset": {"id": "test", "case_count": 1}, "parameters": {"runs": 1},
            "reference_checks": self.checks()}
        self.write_json(self.run / "manifest.json", manifest)
        artifact = self.run / "artifacts/output.txt"
        artifact.parent.mkdir()
        artifact.write_text("Provider raw output stays unchanged")
        record = {"attempt_id": "p__body__r001", "case_id": "body", "provider": "p",
            "ok": True, "started_at": self.now.isoformat(), "elapsed_ms": 1000,
            "artifact_path": "artifacts/output.txt", "metrics": {
                "reference_sha256": sha256(self.case.ground_truth_main_content),
                "content_sha256": sha256(artifact.read_bytes())}}
        (self.run / "results.jsonl").write_text(json.dumps(record) + "\n")
        return manifest

    def observation(self, manifest, **updates):
        row = {"case_id": "body", "independent": True, "content_text": self.case.ground_truth_main_content,
            "checked_at": (self.now + timedelta(seconds=3)).isoformat(),
            "scope_identity": deepcopy(manifest["reference_checks"]["cases"]["body"]["scope_identity"]),
            "completion": {"satisfied": True, "elapsed_ms": 100, "timeout_ms": 1000}}
        row.update(updates)
        return row

    def finalize(self, rows, now=None):
        path = self.root / "observations.json"
        self.write_json(path, rows)
        return finalize_reference_checks(self.run, path, now=now or self.now + timedelta(seconds=4))

    def test_timezone_age_and_future_clocks(self):
        for value in (None, "garbage", "2026-09-03T11:59:00", self.now.replace(tzinfo=None),
                      (self.now + timedelta(microseconds=1)).isoformat(),
                      (self.now - timedelta(minutes=30, microseconds=1)).isoformat()):
            with self.subTest(value=value):
                case = replace(self.case, source_verification={**self.case.source_verification, "checked_at": value})
                with self.assertRaises(ReferenceLifecycleError):
                    self.checks(case)
        case = replace(self.case, source_verification={**self.case.source_verification,
            "checked_at": (self.now - timedelta(minutes=30)).isoformat()})
        self.assertEqual(self.checks(case)["status"], "awaiting_postcheck")
        with self.assertRaises(ReferenceLifecycleError):
            preflight_reference_checks([self.case], self.dataset_path, now=self.now.replace(tzinfo=None))

    def test_hash_is_exact_not_just_normalized(self):
        with self.assertRaisesRegex(ReferenceLifecycleError, "SHA-256"):
            self.checks(replace(self.case, ground_truth_main_content="Price  10 dollars"))

    def test_only_body_with_independent_metadata_is_required(self):
        for case in (replace(self.case, source_verification={}), replace(self.case, track="image"),
                     replace(self.case, task_kind="structure"), replace(self.case, ground_truth_main_content="")):
            self.assertIsNone(self.checks(case))
        self.assertIsNotNone(self.checks(replace(self.case, track="challenge")))

    def test_per_case_rechecks_clock_and_immutable_scope(self):
        binding = self.checks()["cases"]["body"]
        with self.assertRaisesRegex(ReferenceLifecycleError, "30 minutes"):
            check_case_reference_fresh(self.case, self.dataset_path, binding, now=self.now + timedelta(minutes=30))
        with self.assertRaisesRegex(ReferenceLifecycleError, "binding changed"):
            check_case_reference_fresh(replace(self.case, task_prompt="Only first sentence"),
                                       self.dataset_path, binding, now=self.now)

    def dynamic_case(self):
        render = {"scope": "Complete article", "rendered_truncated": False,
            "completion": {"condition": "Full article visible", "satisfied": True,
                "elapsed_ms": 100, "timeout_ms": 1000, "checked_at": self.now.isoformat()}}
        case = replace(self.case, track="dynamic", required_facts=[{"id": "price", "alternatives": ["Price"]}],
                       source_benchmark={"capture_scope": "Complete article", "render_evidence": render})
        folder = self.root / "sources/body"
        folder.mkdir(parents=True)
        contents = {"initial.html.txt": "<main>Loading</main>",
                    "rendered.txt": case.ground_truth_main_content, "reference.txt": case.ground_truth_main_content}
        for name, text in contents.items():
            (folder / name).write_text(text)
        self.write_json(folder / "evidence.json", {"case_id": "body", "url": case.url,
            "completion": "Complete article", "checked_at": self.now.isoformat(),
            "initial_checked_at": self.now.isoformat(), "initial_sha256": sha256(contents["initial.html.txt"]),
            "rendered_sha256": sha256(contents["rendered.txt"]), "reference_sha256": sha256(contents["reference.txt"])})
        return case

    def test_dynamic_requires_complete_hash_bound_pair(self):
        case = self.dynamic_case()
        self.assertIn("source_files_sha256", self.checks(case)["cases"]["body"])
        (self.root / "sources/body/rendered.txt").write_text("Price")
        with self.assertRaisesRegex(ReferenceLifecycleError, "hash/content"):
            self.checks(case)

    def test_dynamic_failed_stale_or_missing_completion(self):
        case = self.dynamic_case()
        for fields in ({"satisfied": False}, {"elapsed_ms": 1001}, {"elapsed_ms": float("nan")},
                       {"checked_at": (self.now - timedelta(minutes=31)).isoformat()}, {"elapsed_ms": None}):
            with self.subTest(fields=fields):
                benchmark = deepcopy(case.source_benchmark)
                benchmark["render_evidence"]["completion"].update(fields)
                with self.assertRaises(ReferenceLifecycleError):
                    self.checks(replace(case, source_benchmark=benchmark))

    def test_dynamic_pair_missing_and_escape_rejected(self):
        case = self.dynamic_case()
        (self.root / "sources/body/initial.html.txt").unlink()
        with self.assertRaises(ReferenceLifecycleError):
            self.checks(case)
        (self.root / "sources/body/initial.html.txt").symlink_to(self.root.parent / "outside")
        with self.assertRaises(ReferenceLifecycleError):
            self.checks(case)

    def mocked_run(self, case=None, **kwargs):
        case = case or self.case
        provider = Mock(name="synthetic provider")
        provider.name, provider.type, provider.min_interval_seconds = "p", "synthetic", 0
        provider.validate.return_value = []
        provider.fetch.return_value = ProviderResult(ok=True, content=case.ground_truth_main_content)
        self.provider = provider
        return run_evaluation(output_dir=self.run, dataset_meta=self.dataset(case), dataset_path=self.dataset_path,
            cases=[case], providers=[provider], provider_config_path=self.root / "providers.json",
            runs=kwargs.get("runs", 1), timeout=1, delay=0)

    def test_runner_stale_preflight_before_output_and_calls(self):
        with patch("harness.web_fetch.reference_lifecycle._now", return_value=self.now + timedelta(minutes=31)):
            with self.assertRaises(ReferenceLifecycleError):
                self.mocked_run()
        self.provider.fetch.assert_not_called()
        self.provider.validate.assert_not_called()
        self.assertFalse(self.run.exists())

    def test_runner_queue_aging_preserves_first_result_not_provider_failure(self):
        clocks = [self.now, self.now, self.now + timedelta(minutes=31)]
        with patch("harness.web_fetch.reference_lifecycle._now", side_effect=clocks):
            with self.assertRaises(ReferenceLifecycleError):
                self.mocked_run(runs=2)
        self.assertEqual(self.provider.fetch.call_count, 1)
        records = [json.loads(line) for line in (self.run / "results.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["ok"])
        self.assertEqual(records[0]["metrics"]["reference_sha256"], sha256(self.case.ground_truth_main_content))
        self.assertEqual(json.loads((self.run / "manifest.json").read_text())["reference_checks"]["status"],
                         "reference_unverified")

    def test_child_preflight_before_output_or_runtime_calls(self):
        self.dataset(replace(self.case, track="dynamic"))
        paths = local.RuntimePaths(*(self.root / name for name in ("cow", "claw", "deepseek", "node")))
        # Stub isolation inspection; never inspect the actual environment.
        with patch.object(local, "os") as operating, patch.object(local, "clean_environment", return_value={}), \
                patch("harness.web_fetch.reference_lifecycle._now", return_value=self.now + timedelta(minutes=31)), \
                patch.object(local.subprocess, "run") as process:
            operating.environ = {}
            with self.assertRaises(ReferenceLifecycleError):
                local.run_child(self.run, paths, "dynamic", self.root / "tmp", self.dataset_path)
        process.assert_not_called()
        self.assertFalse(self.run.exists())

    def test_formatting_only_accepted_and_evidence_pointer_hash(self):
        original = self.prepare_run()
        immutable = {p: p.read_bytes() for p in (self.run / "results.jsonl", self.run / "dataset.json",
                                                  self.run / "artifacts/output.txt")}
        row = self.observation(original, content_text="**Price**   10\n dollars")
        result = self.finalize([row])
        self.assertEqual(result["status"], "verified")
        evidence = self.run / result["evidence_path"]
        self.assertTrue(evidence.resolve().is_relative_to(self.run.resolve()))
        self.assertEqual(sha256(evidence.read_bytes()), result["evidence_sha256"])
        current = json.loads((self.run / "manifest.json").read_text())
        self.assertEqual(current["reference_checks"]["cases"], original["reference_checks"]["cases"])
        for key in original.keys() - {"reference_checks"}:
            self.assertEqual(current[key], original[key])
        for path, before in immutable.items():
            self.assertEqual(path.read_bytes(), before)

    def test_changed_content_cannot_finalize_or_retry_to_hide_change(self):
        original = self.prepare_run()
        self.assertEqual(self.finalize([self.observation(original, content_text="Price 11 dollars")])["status"],
                         "reference_changed")
        with self.assertRaisesRegex(ReferenceLifecycleError, "immutable"):
            self.finalize([self.observation(original)])
        self.assertEqual(json.loads((self.run / "manifest.json").read_text())["reference_checks"]["status"],
                         "reference_changed")

    def test_missing_duplicate_unknown_observations_rejected(self):
        original = self.prepare_run()
        row = self.observation(original)
        for rows in ([], [row, row], [row, {**row, "case_id": "unknown"}]):
            with self.subTest(rows=rows):
                self.write_json(self.run / "manifest.json", original)
                self.assertEqual(self.finalize(rows)["status"], "reference_unverified")

    def test_postcheck_time_bounds_and_completion(self):
        original = self.prepare_run()
        for updates in ({"checked_at": self.now.isoformat()},
                        {"checked_at": (self.now + timedelta(seconds=1)).isoformat()},
                        {"checked_at": (self.now + timedelta(minutes=31)).isoformat()},
                        {"checked_at": "2026-09-03T12:00:03"},
                        {"completion": {"satisfied": False, "elapsed_ms": 100, "timeout_ms": 1000}},
                        {"completion": {"satisfied": True, "elapsed_ms": 1001, "timeout_ms": 1000}},
                        {"completion": {"satisfied": True, "elapsed_ms": True, "timeout_ms": 1000}},
                        {"independent": False}, {"scope_identity": {}}, {"content_sha256": "bad"}):
            with self.subTest(updates=updates):
                self.write_json(self.run / "manifest.json", original)
                self.assertEqual(self.finalize([self.observation(original, **updates)])["status"], "reference_unverified")

    def test_postcheck_late_capture_and_stale_submission(self):
        original = self.prepare_run()
        for observation_time, finalizer_time in ((timedelta(minutes=31), timedelta(minutes=32)),
                                                 (timedelta(seconds=3), timedelta(minutes=31))):
            self.write_json(self.run / "manifest.json", original)
            result = self.finalize([self.observation(original, checked_at=(self.now + observation_time).isoformat())],
                                   now=self.now + finalizer_time)
            self.assertEqual(result["status"], "reference_unverified")

    def test_latest_relevant_call_across_providers(self):
        original = self.prepare_run()
        records_path = self.run / "results.jsonl"
        record = json.loads(records_path.read_text())
        second = {**record, "attempt_id": "p2__body__r001", "started_at": (self.now + timedelta(seconds=4)).isoformat()}
        records_path.write_text(json.dumps(record) + "\n" + json.dumps(second) + "\n")
        original.update(attempt_count=2, attempt_ids=[record["attempt_id"], second["attempt_id"]],
                        completed_at=(self.now + timedelta(seconds=6)).isoformat())
        self.write_json(self.run / "manifest.json", original)
        self.assertEqual(self.finalize([self.observation(original)], now=self.now + timedelta(seconds=7))["status"],
                         "reference_unverified")

    def test_execution_batches_and_manifest_updated_consistently_legacy_untouched(self):
        original = self.prepare_run()
        legacy = {"batch_id": "legacy", "note": "Historical; no freshness claim"}
        self.write_json(self.run / "execution.json", {"batches": [original, legacy], "original": "metadata"})
        self.assertEqual(self.finalize([self.observation(original)])["status"], "verified")
        execution = json.loads((self.run / "execution.json").read_text())
        manifest = json.loads((self.run / "manifest.json").read_text())
        self.assertEqual(execution["batches"], [manifest, legacy])
        self.assertEqual(execution["original"], "metadata")

    def test_evidence_symlink_escape_rejected(self):
        original = self.prepare_run()
        (self.run / "reference-checks").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.finalize([self.observation(original)])
        self.assertEqual(json.loads((self.run / "manifest.json").read_text()), original)

    def test_legacy_cannot_gain_certificate_and_new_run_cannot_reuse_old_one(self):
        original = self.prepare_run()
        self.finalize([self.observation(original)])
        with self.assertRaises(ReferenceLifecycleError):
            preflight_reference_checks([self.case], self.dataset_path, now=self.now + timedelta(minutes=31))
        original.pop("reference_checks")
        self.write_json(self.run / "manifest.json", original)
        with self.assertRaisesRegex(ReferenceLifecycleError, "historical"):
            self.finalize([])
        self.assertNotIn("reference_checks", json.loads((self.run / "manifest.json").read_text()))

    def test_rescore_rejects_changed_reference_or_capture_before_writes(self):
        self.prepare_run()
        before = {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        for case in (replace(self.case, ground_truth_main_content="Price 11 dollars"),
                     replace(self.case, source_verification={**self.case.source_verification, "checked_at": self.now.isoformat()}),
                     replace(self.case, task_prompt="Different scope"),
                     replace(self.case, required_facts=[] if self.case.required_facts else [{"id": "new", "alternatives": ["Price"]}]),
                     replace(self.case, required_patterns=[{"id": "new", "pattern": "Price"}]),
                     replace(self.case, forbidden_any=["Price"]), replace(self.case, min_content_chars=0),
                     replace(self.case, fact_coverage_threshold=0.1), replace(self.case, expected_any=[])):
            with self.assertRaises(ReferenceLifecycleError):
                rescore_evaluation(run_dir=self.run, dataset_meta=self.dataset(case), dataset_path=self.dataset_path, cases=[case])
            for path, raw in before.items():
                self.assertEqual(path.read_bytes(), raw)

    def test_rescore_preserves_binding_without_claiming_current_freshness(self):
        original = self.prepare_run()
        with patch("harness.web_fetch.runner.summarize", return_value={}):
            records, _ = rescore_evaluation(run_dir=self.run, dataset_meta=self.dataset(),
                                           dataset_path=self.dataset_path, cases=[self.case])
        self.assertEqual(records[0]["metrics"]["reference_sha256"], sha256(self.case.ground_truth_main_content))
        self.assertEqual(json.loads((self.run / "manifest.json").read_text())["reference_checks"], original["reference_checks"])

    def verified_run(self):
        original = self.prepare_run()
        self.finalize([self.observation(original)])
        return json.loads((self.run / "manifest.json").read_text())

    def test_public_guard_portable_binding_and_source_evidence(self):
        manifest = self.verified_run()
        records = [json.loads((self.run / "results.jsonl").read_text())]
        # Whole-run byte hashes are provenance, not equality gates after merge.
        self.write_json(self.run / "dataset.json", {"cases": [self.case.__dict__], "merged": True})
        require_verified_reference_checks([manifest, {"historical": True}],
            cases={"body": self.case}, records=records, source=self.run)
        require_verified_reference_checks(manifest, cases=self.dataset())

    def test_public_guard_does_not_trust_status_alone(self):
        manifest = self.verified_run()
        mutations = [
            lambda c: c.pop("postcheck"),
            lambda c: c.update(status="awaiting_postcheck"),
            lambda c: c["postcheck"].update(status="reference_unverified"),
            lambda c: c["postcheck"].update(cases={}),
            lambda c: c["postcheck"]["cases"]["body"].update(status="reference_changed"),
            lambda c: c["postcheck"]["cases"]["body"].update(normalized_sha256="0" * 64),
            lambda c: c["postcheck"].update(evidence_path="../observations.json"),
        ]
        for mutate in mutations:
            candidate = deepcopy(manifest)
            mutate(candidate["reference_checks"])
            with self.assertRaises(ReferenceLifecycleError):
                require_verified_reference_checks([candidate])
        for value in (None, {}, "verified"):
            with self.assertRaises(ReferenceLifecycleError):
                require_verified_reference_checks([{"reference_checks": value}])

    def test_public_guard_rejects_changed_rules_records_and_evidence(self):
        manifest = self.verified_run()
        with self.assertRaises(ReferenceLifecycleError):
            require_verified_reference_checks([manifest], cases=[replace(self.case, min_content_chars=0)])
        records = [json.loads((self.run / "results.jsonl").read_text())]
        records[0]["metrics"]["reference_sha256"] = "0" * 64
        with self.assertRaises(ReferenceLifecycleError):
            require_verified_reference_checks([manifest], records=records)
        path = self.run / manifest["reference_checks"]["postcheck"]["evidence_path"]
        path.write_text("tampered")
        with self.assertRaisesRegex(ReferenceLifecycleError, "evidence hash"):
            require_verified_reference_checks([manifest], source=self.run)

    def test_guard_keeps_historical_certificates_readable_without_current_freshness(self):
        manifest = self.verified_run()
        with patch("harness.web_fetch.reference_lifecycle._now", side_effect=AssertionError("Must not ask today's clock")):
            require_verified_reference_checks([manifest], cases=[self.case], source=self.run)
            require_verified_reference_checks([{"legacy": True}])

    def test_legacy_rescore_still_allowed_without_claiming_protocol(self):
        manifest = self.prepare_run()
        del manifest["reference_checks"]
        self.write_json(self.run / "manifest.json", manifest)
        record = json.loads((self.run / "results.jsonl").read_text())
        del record["metrics"]["reference_sha256"]
        (self.run / "results.jsonl").write_text(json.dumps(record) + "\n")
        changed = replace(self.case, ground_truth_main_content="Updated historical scoring reference")
        with patch("harness.web_fetch.runner.summarize", return_value={}):
            records, _ = rescore_evaluation(run_dir=self.run, dataset_meta=self.dataset(changed),
                                           dataset_path=self.dataset_path, cases=[changed])
        self.assertNotIn("reference_sha256", records[0]["metrics"])
        self.assertNotIn("reference_checks", json.loads((self.run / "manifest.json").read_text()))


if __name__ == "__main__":
    unittest.main()
