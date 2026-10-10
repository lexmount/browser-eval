import copy
import unittest
from pathlib import Path

from harness.lexbench import resource_profiles as profiles


class LinuxResourceProfileTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(profiles.__file__)
        configs = {
            name: {
                "layout": layout,
                "target": "aarch64-unknown-linux-gnu",
                "profile": "release",
                "sha256": "a" * 64,
                "binary": "/synthetic/browser",
                "version": "fixture",
            }
            for name, layout in (
                ("official_off", "off"),
                ("official_on", "on"),
                ("fixed_off", "off"),
                ("fixed_on", "on"),
            )
        }
        self.protocol = {
            "id": "fixture",
            "max_observer_effect_pct": 10,
            "seed": "fixed",
            "image": "sha256:" + "a" * 64,
            "runner_root": "/synthetic/runner",
            "node_modules": "/synthetic/modules",
            "node_modules_lock": "/synthetic/modules/.package-lock.json",
            "agent_browser_binary": "/synthetic/agent",
            "input_files": {"manifest.json": "a" * 64},
            "chart_keys": [
                {"task_id": f"task_{i}", "attempt": n} for i in range(209) for n in range(1, 6)
            ],
            "schema": "moli_linux_resource_profiles/1",
            "configs": configs,
            "task_ids": [f"task_{i}" for i in range(557)],
            "attempts": 5,
            "jobs": 1,
            "sample_interval_ms": 250,
            "memory_metric": "process_tree_pss",
            "orchestrator_sha256": profiles.sha256(self.source),
            "order": [
                *[[name, "baseline"] for name in configs],
                *[[name, "engine"] for name in reversed(configs)],
            ],
        }

    def test_complete_official_resource_scope_is_accepted(self):
        profiles.validate_protocol(self.protocol, self.source)

    def test_partial_scope_parallel_execution_and_changed_collector_are_rejected(self):
        for field, value in (
            ("task_ids", self.protocol["task_ids"][:-1]),
            ("task_ids", ["duplicate"] * 557),
            ("attempts", 3),
            ("jobs", 2),
            ("memory_metric", "process_tree_rss"),
            ("sample_interval_ms", 500),
            ("orchestrator_sha256", "b" * 64),
        ):
            candidate = {**self.protocol, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                profiles.validate_protocol(candidate, self.source)

    def test_missing_calibration_or_native_macos_build_is_rejected(self):
        candidate = copy.deepcopy(self.protocol)
        candidate["order"] = candidate["order"][1:]
        with self.assertRaises(ValueError):
            profiles.validate_protocol(candidate, self.source)
        for field, value in (("target", "aarch64-apple-darwin"), ("profile", "debug")):
            candidate = copy.deepcopy(self.protocol)
            candidate["configs"]["fixed_on"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                profiles.validate_protocol(candidate, self.source)
