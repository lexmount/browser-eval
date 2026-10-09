"""Synthetic trajectory evidence shared by behavior and installed-package checks."""

import hashlib
import json
from pathlib import Path


def write_report_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    (source / "run").mkdir(parents=True)
    manifest = {
        "run_id": "run",
        "suite_sha256": "suite",
        "protocol": {"engines": ["chromium", "moli"]},
        "environment": {},
        "suite": [{"task_id": "task"}],
    }
    summary = {
        "functional": {
            "chromium": {"tasks": 1, "physical_attempts": 1, "result_final_passes": 1},
            "moli": {"tasks": 1, "physical_attempts": 1, "result_final_passes": 1},
        },
        "by_task": {
            "task": {
                "attempts": {
                    engine: [
                        {
                            "attempt": 1,
                            "is_retry": False,
                            "status": "pass",
                            "result_status": "pass",
                            "process_status": "pass",
                            "combined_status": "pass",
                            "evidence_status": "pass",
                            "error": "",
                            "process_validation": {
                                "expected_actions": ["click"],
                                "actual_actions": ["click"],
                                "all_calls_passed": True,
                                "passed": True,
                            },
                            "validation": [{"status": "success", "score": 1.0}],
                            "trace": [
                                {"action": "click", "status": "pass"},
                                {
                                    "action": "evaluate_result",
                                    "status": "pass",
                                    "checks": [{"status": "success", "score": 1.0}],
                                },
                            ],
                            "network_har": {"status": "complete"},
                            "final_page_evidence": {"status": "complete"},
                        }
                    ]
                    for engine in ("chromium", "moli")
                }
            }
        },
        "by_complexity": {},
        "resource_comparison": {},
    }
    (source / "run/manifest.json").write_text(json.dumps(manifest))
    (source / "run/summary.json").write_text(json.dumps(summary))
    profile = tmp_path / "evaluation/browser-use-public/current-report.json"
    profile.parent.mkdir(parents=True)
    authority = {
        "evaluation_kind": "fixed_trajectory_replay",
        "agentic_browser_use": False,
        "task_count_per_engine": 1,
        "engines": ["chromium", "moli"],
        "run_id": "run",
        "suite_sha256": "suite",
        "evidence": {
            name + "_sha256": hashlib.sha256(
                (source / "run" / (name + ".json")).read_bytes()
            ).hexdigest()
            for name in ("manifest", "summary")
        },
        "results": {engine: {"passes": 1} for engine in ("chromium", "moli")},
        "publication": {"version_id": "unchanged"},
    }
    profile.write_text(json.dumps(authority))
    return source, profile


def authoring_suite_fixture() -> tuple[dict, dict]:
    """One valid pending non-mutating task; no browser or evaluator calls."""
    suite = {"schema": "lex_browser_use_tasks/4", "benchmark": {}, "tasks": [{
        "id": "webarena-1", "title": "Read the example page", "difficulty": "simple",
        "tags": ["retrieve"], "environment": {"kind": "self_hosted", "sites": ["map"]},
        "source": {"url": "https://example.test/source"},
        "evaluator": {"kind": "webarena_verified", "task_id": 1, "revision": 0},
        "trajectory": {"schema": "lex_browser_trajectory/1", "provenance": {"kind": "official"},
                       "actions": [{"action": "navigate", "url": "https://example.test/",
                                    "save_as": "page"}]},
        "ground_truth": {"expected_actions": ["navigate"], "qualification": {"status": "pending"}},
    }]}
    qualification = {"tasks": [{"task_id": "webarena-1", "disposition": "needs_trace_repair"}]}
    return suite, qualification
