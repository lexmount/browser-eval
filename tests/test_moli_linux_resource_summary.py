import copy
import unittest

from harness.lexbench.resource_summary import validate_summary


class LinuxResourceSummaryTests(unittest.TestCase):
    def setUp(self):
        tasks = [f"task_{i}" for i in range(557)]
        chart = [{"task_id": t, "attempt": a} for t in tasks[:209] for a in range(1, 6)]
        self.builds = {
            name: {
                "sha256": str(i) * 64,
                "target": "aarch64-unknown-linux-gnu",
                "profile": "release",
            }
            for i, name in enumerate(("official_off", "official_on", "fixed_off", "fixed_on"))
        }
        configurations, measurements = {}, {}
        for i, (name, build) in enumerate(self.builds.items()):
            measurements[name] = [
                {
                    "task_id": t,
                    "attempt": a,
                    "resource": {
                        "pss_peak_bytes": (100 + i) * 1024**2,
                        "cpu_total_ms": 10 + i,
                        "measurement_backend": {
                            "memory_pss": "proc_smaps_rollup",
                            "cpu": "proc_tree",
                        },
                        "sample_interval_ms": 250,
                    },
                }
                for t in tasks
                for a in range(1, 6)
            ]
            configurations[name] = {
                "build": build,
                "publishable": True,
                "calibration": {"acceptable": True, "complete_pairing": True},
                "host": {"baseline": {"polluted": False}, "engine": {"polluted": False}},
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
                        for metric, value in (("pss_peak_mib", 100 + i), ("cpu_time_ms", 10 + i))
                    }
                    for scope, n in (
                        ("all_predeclared_calls", 2785),
                        ("frozen_historical_common_pass_calls", 1045),
                    )
                },
            }
        self.summary = {
            "schema": "moli_linux_resource_summary/1",
            "publishable": True,
            "protocol_sha256": "a" * 64,
            "population": {"task_ids": tasks, "chart_keys": chart, "attempts_per_task": 5},
            "configurations": configurations,
            "measurements": measurements,
            "receipt": {
                "schema": "moli_linux_resource_profiles_receipt/1",
                "protocol_sha256": "a" * 64,
                "results": {
                    f"{m}:{p}": {"rows": 2785} for m in self.builds for p in ("baseline", "engine")
                },
            },
        }

    def test_complete_measurements_reproduce_both_population_statistics(self):
        validate_summary(self.summary, self.builds)

    def test_missing_duplicate_or_changed_measurements_are_rejected(self):
        for mutation in (
            lambda s: s["measurements"]["fixed_on"].pop(),
            lambda s: s["measurements"]["fixed_on"].append(s["measurements"]["fixed_on"][0]),
            lambda s: s["measurements"]["fixed_on"][0]["resource"].update(cpu_total_ms=99999),
            lambda s: s["measurements"]["fixed_on"][0]["resource"]["measurement_backend"].update(
                memory_pss="darwin_ps_process_tree_rss"
            ),
            lambda s: s["configurations"]["fixed_on"]["calibration"].update(acceptable=False),
            lambda s: s["configurations"]["fixed_on"]["host"].clear(),
            lambda s: s["configurations"]["fixed_on"]["build"].update(profile="debug"),
            lambda s: s["receipt"]["results"].pop("fixed_on:baseline"),
        ):
            changed = copy.deepcopy(self.summary)
            mutation(changed)
            with self.assertRaises(ValueError):
                validate_summary(changed, self.builds)
