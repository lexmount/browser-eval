import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.package_headless_speed import build_summary, derive_aggregate, main


class SpeedPackagingTests(unittest.TestCase):
    @staticmethod
    def execution(providers, cases):
        batches=[]
        for provider in providers:
            attempt_ids=[f"{provider}__{case_id}__r001" for case_id in cases]
            batches.append({
                "providers":[{"name":provider}], "attempt_ids":attempt_ids,
                "attempt_count":len(attempt_ids),
                "started_at":"2026-01-01T00:00:01+00:00",
                "completed_at":"2026-01-01T00:00:03+00:00",
            })
        aggregate={
            "providers":providers, "case_ids":cases, "batch_count":len(batches),
            "started_at":"2026-01-01T00:00:00+00:00",
            "completed_at":"2026-01-01T00:00:04+00:00",
        }
        return batches,aggregate

    def test_keeps_predeclared_sample_without_an_all_method_pass(self):
        dataset = {
            "coverage_plan":{"web":["one","two"]},
            "cases": [
                {"id": "a", "task_kind": "body", "track":"web"},
                {"id": "b", "task_kind": "body", "track":"web"},
            ]
        }
        records = []
        for provider, passing_case in (("one", "a"), ("two", "b")):
            for index, case_id in enumerate(("a", "b"), 1):
                records.append(
                    {
                        "provider": provider,
                        "case_id": case_id,
                        "attempt_id":f"{provider}__{case_id}__r001",
                        "run":1,
                        "ok": True,
                        "elapsed_ms": index * 100,
                        "metrics": {"content_pass": case_id == passing_case},
                    }
                )

        batches,aggregate=self.execution(["one","two"],["a","b"])
        summary = build_summary(dataset, records, batches, aggregate)

        self.assertEqual(summary["schema_version"], 2)
        self.assertIn("2 single observations", summary["percentile_limit"])
        self.assertEqual(summary["common_pass_cases"], [])
        self.assertEqual([row["planned_attempts"] for row in summary["rows"]], [2, 2])
        self.assertEqual([row["successful_attempts"] for row in summary["rows"]], [1, 1])
        self.assertEqual([row["all_calls_p50_ms"] for row in summary["rows"]], [150, 150])
        self.assertEqual([row["successful_calls_p50_ms"] for row in summary["rows"]], [100, 200])

    def test_rejects_an_incomplete_matrix(self):
        dataset = {"coverage_plan":{"web":["one","two"]},
                   "cases": [{"id": "a", "task_kind": "body", "track":"web"}]}
        records = [
            {
                "provider": "one",
                "case_id": "a",
                "attempt_id":"one__a__r001", "run":1,
                "ok": True,
                "elapsed_ms": 100,
                "metrics": {"content_pass": True},
            },
            {
                "provider": "two",
                "case_id": "missing",
                "attempt_id":"two__missing__r001", "run":1,
                "ok": True,
                "elapsed_ms": 100,
                "metrics": {"content_pass": True},
            },
        ]

        with self.assertRaisesRegex(ValueError, "complete provider/case matrix"):
            build_summary(dataset, records, [], {})

    def test_rejects_missing_provider_and_nonoverlapping_batches(self):
        dataset={"coverage_plan":{"web":["one","two"]},
                 "cases":[{"id":"a","track":"web","task_kind":"body"}]}
        one={"provider":"one","case_id":"a","attempt_id":"one__a__r001",
             "run":1,"ok":True,"elapsed_ms":1,"metrics":{"content_pass":True}}
        batches,aggregate=self.execution(["one","two"],["a"])
        with self.assertRaisesRegex(ValueError,"complete provider/case matrix"):
            build_summary(dataset,[one],batches,aggregate)
        records=[one,{**one,"provider":"two","attempt_id":"two__a__r001"}]
        batches[1]["started_at"]="2026-01-01T00:00:05+00:00"
        batches[1]["completed_at"]="2026-01-01T00:00:06+00:00"
        aggregate["completed_at"]="2026-01-01T00:00:07+00:00"
        with self.assertRaisesRegex(ValueError,"common execution window"):
            build_summary(dataset,records,batches,aggregate)

    def test_packager_requires_versioned_aggregate_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"dataset.json").write_text(json.dumps({"cases":[]}))
            (root/"results.jsonl").write_text("")
            with patch("sys.argv",["package_headless_speed.py","--run",str(root)]):
                with self.assertRaisesRegex(ValueError,"aggregate execution.json"):
                    main()
            (root/"execution.json").write_text(json.dumps({"schema_version":99}))
            with patch("sys.argv",["package_headless_speed.py","--run",str(root)]):
                with self.assertRaisesRegex(ValueError,"execution schema"):
                    main()

    def test_legacy_aggregate_is_derived_only_from_batch_bounds(self):
        dataset={"coverage_plan":{"web":["one","two"]},
                 "cases":[{"id":"b","track":"web"},{"id":"a","track":"web"}]}
        batches=[
            {"started_at":"2026-01-01T00:00:02Z",
             "completed_at":"2026-01-01T00:00:04Z"},
            {"started_at":"2026-01-01T00:00:01+00:00",
             "completed_at":"2026-01-01T00:00:03+00:00"},
        ]
        self.assertEqual(derive_aggregate(dataset,batches),{
            "started_at":"2026-01-01T00:00:01+00:00",
            "completed_at":"2026-01-01T00:00:04+00:00",
            "providers":["one","two"], "case_ids":["b","a"], "batch_count":2,
        })

    def test_explicit_legacy_upgrade_validates_then_writes_schema_v1(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            dataset={"coverage_plan":{"web":["one"]},
                     "cases":[{"id":"a","track":"web","task_kind":"body"}]}
            record={"provider":"one","case_id":"a","attempt_id":"one__a__r001",
                    "run":1,"ok":True,"elapsed_ms":5,"metrics":{"content_pass":True}}
            batch={"providers":[{"name":"one"}],"attempt_ids":["one__a__r001"],
                   "attempt_count":1,"started_at":"2026-01-01T00:00:01Z",
                   "completed_at":"2026-01-01T00:00:02Z"}
            (root/"dataset.json").write_text(json.dumps(dataset))
            (root/"results.jsonl").write_text(json.dumps(record)+"\n")
            (root/"execution.json").write_text(json.dumps({"batches":[batch]}))
            argv=["package_headless_speed.py","--run",str(root),
                  "--upgrade-execution-metadata"]
            with patch("sys.argv",argv), \
                    patch("scripts.package_headless_speed.require_verified_reference_checks"):
                main()
            execution=json.loads((root/"execution.json").read_text())
            self.assertEqual(execution["schema_version"],1)
            self.assertEqual(execution["aggregate"]["providers"],["one"])
            self.assertEqual(execution["aggregate"]["started_at"],
                             "2026-01-01T00:00:01+00:00")
            self.assertEqual(json.loads((root/"speed-comparison.json").read_text())
                             ["schema_version"],2)


if __name__ == "__main__":
    unittest.main()
