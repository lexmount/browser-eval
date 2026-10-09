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


def write_supersession_fixture(root: Path) -> tuple[Path, Path]:
    """A complete two-browser cohort and a complete two-call corrective rerun."""
    runs = root / "base", root / "correction"
    for directory, engines in zip(runs, (("chromium", "moli"), ("moli",)), strict=True):
        directory.mkdir(parents=True)
        rows = []
        for engine in engines:
            statuses = ["pass"] if engine == "chromium" else ["fail", "pass" if directory == runs[1] else "fail"]
            for attempt, status in enumerate(statuses, 1):
                evidence = directory / "tasks/case-a" / engine / f"attempt-{attempt}"
                evidence.mkdir(parents=True)
                screenshot, page, har = [evidence / name for name in (
                    "final.png", "final-page.json", "network.har")]
                screenshot.write_bytes(b"synthetic screenshot")
                page.write_text('{}')
                har.write_text('{"log":{"entries":[]}}')
                checks = [{"status": "success" if status == "pass" else "failure",
                           "score": 1 if status == "pass" else 0}]
                row = {
                    "task_id": "case-a", "engine": engine, "title": "Synthetic task",
                    "complexity": "step_01_05", "tags": [], "step_count": 1,
                    "attempt": attempt, "is_retry": attempt > 1, "status": status,
                    "process_status": "pass", "result_status": status,
                    "combined_status": status, "evidence_status": "pass",
                    "duration_ms": 10, "resource": {}, "output_chars": 1, "tool_output_tokens": 1,
                    "error": "" if status == "pass" else "synthetic evaluator rejection",
                    "task_contract_sha256": "a" * 64, "evidence_directory": str(evidence),
                    "trace": [{"action": "click", "status": "pass", "index": 1},
                              {"action": "evaluate_result", "status": status, "checks": checks}],
                    "process_validation": {"expected_actions": ["click"], "actual_actions": ["click"],
                                           "all_calls_passed": True, "passed": True},
                    "validation": checks, "network_har": {"status": "complete"},
                    "final_page_evidence": {"status": "complete", **{
                        key: {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for key, path in (("screenshot", screenshot), ("page", page))}},
                }
                (evidence / "attempt.json").write_text(json.dumps(row))
                rows.append(row)
        raw = ''.join(json.dumps(row) + '\n' for row in rows).encode()
        (directory / "attempts.jsonl").write_bytes(raw)
        manifest = {
            "run_id": directory.name, "suite_sha256": "b" * 64,
            "suite": [{"task_id": "case-a", "title": "Synthetic task", "step_count": 1,
                       "tags": [], "complexity": "step_01_05", "difficulty": "simple"}],
            "protocol": {"engines": list(engines), "max_failure_retries": 1,
                         "sample_interval_ms": 250, "post_reset_settle_ms": 0,
                         "task_timeout_ms": 1000, "cpus_per_browser": 1, "memory_per_browser": "1g"},
            "environment": {"images": {engine: {"id": "image-" + engine} for engine in engines},
                            "browser_versions": {engine: "synthetic" for engine in engines}},
            "artifact_provenance": {"attempts_sha256": hashlib.sha256(raw).hexdigest()},
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
    return runs


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
