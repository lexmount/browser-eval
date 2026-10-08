"""Resource publication must use the complete declared release comparison."""

import copy
import unittest

from harness.lexbench.resource_evidence import validate_matrix
from harness.lexbench.resource_matrix import schedule


class ResourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        methods = ["119_official_off", "119_official_on", "119_off", "119_on"]
        self.builds = {
            name: {
                "sha256": ("a" if "official" in name else "b") * 64,
                "source_commit": "source",
                "profile": "release",
                "target": "arm64",
            }
            for name in methods
        }
        protocol = {
            "schema": "moli_resource_matrix_protocol/1",
            "selected_round": 1,
            "task_ids": [f"case_{i}" for i in range(557)],
            "attempts": 5,
            "sampler_interval_ms": 250,
            "orchestrator_sha256": "c" * 64,
            "reference_protocol_sha256": "e" * 64,
            "chart_keys": [
                {"task_id": f"case_{i}", "attempt": a} for i in range(209) for a in range(1, 6)
            ],
            "methods": {
                m: {**self.builds[m], "layout": m.rsplit("_", 1)[1], "run_id": m} for m in methods
            },
        }
        calls = [
            {
                "method": m,
                "task_id": t,
                "attempt": a,
                "started_ns": i * 2,
                "ended_ns": i * 2 + 1,
                "binary_sha256": self.builds[m]["sha256"],
            }
            for i, (m, t, a) in enumerate(schedule(protocol["task_ids"], 5, methods))
        ]
        results = {
            m: {k: "d" * 64 for k in ("run_manifest.json", "results.jsonl", "host_summary.json")}
            for m in methods
        }
        self.bundle = {
            "protocol": protocol,
            "calls": calls,
            "measurements": {
                m: [
                    {
                        "task_id": task,
                        "attempt": a,
                        "rss_peak_bytes": 100 * 1024**2,
                        "cpu_total_ms": 10,
                        "memory_backend": "darwin_ps_process_tree_rss",
                    }
                    for task in protocol["task_ids"]
                    for a in range(1, 6)
                ]
                for m in methods
            },
            "receipt": {
                "schema": "moli_resource_matrix_receipt/1",
                "selected_round": 1,
                "physical_calls": 11140,
                "orchestrator_sha256": "c" * 64,
                "results": results,
            },
        }
        self.summaries = {}
        for group in ("official", "patched"):
            ids = methods[:2] if group == "official" else methods[2:]
            build = self.builds[ids[0]]
            self.summaries[group] = {
                "provenance": {"comparison_protocol_sha256": "e" * 64},
                "candidate": {**build, "binary_sha256": build["sha256"]},
                "configurations": {
                    m.rsplit("_", 1)[1]: {
                        "metrics": {
                            scope: {
                                metric: {
                                    "n": n,
                                    "sum": n * value,
                                    "mean": value,
                                    "p50": value,
                                    "p95": value,
                                    "min": value,
                                    "max": value,
                                }
                                for metric, value in (("rss_peak_mib", 100), ("cpu_time_ms", 10))
                            }
                            for scope, n in (
                                ("all_predeclared_calls", 2785),
                                ("frozen_historical_common_pass_calls", 1045),
                            )
                        },
                        "provenance": {
                            "profiled": {
                                "run_id": m,
                                "manifest_sha256": "d" * 64,
                                "results_sha256": "d" * 64,
                                "host_summary_sha256": "d" * 64,
                            }
                        },
                    }
                    for m in ids
                },
            }

    def test_complete_release_matrix_can_retain_separate_functional_evidence(self):
        validate_matrix(self.bundle, self.builds, self.summaries)

    def test_partial_reordered_overlapping_or_different_build_calls_cannot_publish(self):
        mutations = [
            lambda b: b["calls"].pop(),
            lambda b: b["calls"].reverse(),
            lambda b: b["calls"][1].update(started_ns=0),
            lambda b: b["protocol"]["methods"]["119_on"].update(profile="debug"),
            lambda b: b["receipt"].update(selected_round=2),
        ]
        for mutate in mutations:
            bundle = copy.deepcopy(self.bundle)
            mutate(bundle)
            with self.assertRaises(ValueError):
                validate_matrix(bundle, self.builds, self.summaries)

    def test_unrelated_measurements_cannot_be_substituted_into_summary(self):
        for key in ("run_id", "results_sha256", "manifest_sha256", "host_summary_sha256"):
            summaries = copy.deepcopy(self.summaries)
            summaries["patched"]["configurations"]["on"]["provenance"]["profiled"][key] = "other"
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_matrix(self.bundle, self.builds, summaries)

    def test_internally_consistent_but_wrong_statistics_cannot_publish(self):
        stats = self.summaries["patched"]["configurations"]["on"]["metrics"][
            "all_predeclared_calls"
        ]["cpu_time_ms"]
        stats.update(mean=20, sum=20 * 2785, p50=20, p95=20, min=20, max=20)
        with self.assertRaisesRegex(ValueError, "physical measurements"):
            validate_matrix(self.bundle, self.builds, self.summaries)

    def test_missing_measurement_or_changed_chart_population_cannot_publish(self):
        for mutate in (
            lambda b: b["measurements"]["119_on"].pop(),
            lambda b: b["protocol"]["chart_keys"].pop(),
            lambda b: b["measurements"]["119_on"][0].update(cpu_total_ms=float("nan")),
            lambda b: b["measurements"]["119_on"][0].update(memory_backend="different"),
        ):
            bundle = copy.deepcopy(self.bundle)
            mutate(bundle)
            with self.assertRaises(ValueError):
                validate_matrix(bundle, self.builds, self.summaries)


if __name__ == "__main__":
    unittest.main()
