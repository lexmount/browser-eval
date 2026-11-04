"""Synthetic retained evidence for complete Lexbench report builds; never runs a browser.

Three scenarios render every report stage: ``minimal`` has no candidate cohort,
``macos`` binds fixed-layout candidates to a serialized macOS resource matrix, and
``linux`` binds them to saved Linux resource profiles. Evidence-rejection guards and
the ``--source`` and missing-template paths are not executed. Evidence paths inside
the inputs stay relative, so the rendered bytes do not depend on the build directory.
"""

import hashlib
import json
import shutil
from pathlib import Path

from harness.lexbench import report
from harness.lexbench.resource_matrix import schedule
from harness.lexbench.resource_summary import stats
from tests.test_resource_calibration import experiment

CANDIDATE_114 = "v4_cdp_emulation_setdefaultbackgroundcoloroverride"
ROUNDS = ("off_baseline", "on_baseline", "off_engine", "on_engine")
EVIDENCE_FILES = ("grader.json", "stdout.log", "stderr.log", "cdp.jsonl", "resource.jsonl", "run.json")
# The report pins these digests for its vendored runtime; Git does not hold those bytes.
VENDOR_ASSETS = {
    "mermaid.min.js": (
        b"synthetic mermaid runtime\n",
        "07e37dfa97b337ccc85365d57eddf99b9706f09db3b59b260d0333b23b343c4b",
    ),
    "mermaid-LICENSE.txt": (
        b"synthetic mermaid license\n",
        "ec9fb67dcb25eccc416ed56e1aab819222c805a2a4bfe4cb19e7556bf2ffde80",
    ),
}
RESOURCE_TASKS = [f"task-{index}" for index in range(557)]
CHART_KEYS = [
    {"task_id": task, "attempt": attempt} for task in RESOURCE_TASKS[:209] for attempt in range(1, 6)
]


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return sha(path.read_bytes())


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return sha(path.read_bytes())


def pin_vendor_assets(monkeypatch) -> None:
    """Accept the synthetic vendored runtime in place of the pinned upstream bytes."""
    pinned = {content: digest for content, digest in VENDOR_ASSETS.values()}
    actual = report.bytes_sha256
    monkeypatch.setattr(report, "bytes_sha256", lambda data: pinned.get(data) or actual(data))


def write_charts_as_utf8(monkeypatch) -> None:
    """Keep snapshots locale-independent until #208 decides on the published ``charts.py``."""
    actual_chart = report.render_chart
    actual_write = Path.write_text

    def write_utf8(self, data, encoding="utf-8", errors=None, newline=None):
        return actual_write(self, data, encoding, errors, newline)

    def render_chart(*args, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_text", write_utf8)
            return actual_chart(*args, **kwargs)

    monkeypatch.setattr(report, "render_chart", render_chart)


def fold_per_call_routes(output: Path) -> None:
    """Join the 372-case and per-call directories so one snapshot stays reviewable."""
    for relative in ("data/cases", "data/fixed-cases", "data/evidence"):
        directory = output / relative
        if directory.is_dir():
            routes = sorted(path for path in directory.rglob("*") if path.is_file())
            (output / f"{relative}.folded").write_bytes(
                b"".join(
                    f"--- {path.relative_to(directory).as_posix()}\n".encode() + path.read_bytes()
                    for path in routes
                )
            )
            shutil.rmtree(directory)


def attempt_status(mode: str, phase: str, index: int, attempt: int) -> str:
    """Only the candidate passes every layout-off attempt, as the report requires."""
    if mode == "off":
        passed = index == 0 or (index % 4 == 1 and attempt == 1)
    else:
        passed = not (index % 7 == 3 or (index % 5 == 2 and attempt == 2))
    if phase == "engine" and index % 11 == 6 and attempt == 3:
        passed = not passed
    return "pass" if passed else "fail"


def failure_record(index: int, attempt: int) -> dict:
    details = (
        "script-reported checks failed",
        "inline assertions failed",
        "answer did not match server-side expectation",
        "timed out at http://127.0.0.1:4100/page reading /tmp/worker/log",
    )
    return {"class": ("assertion", "timeout")[index % 2], "detail": details[(index + attempt) % 4]}


def write_call_evidence(run: Path, task: str, attempt: int) -> str:
    """Retain the complete artifact set of one call and return its run-relative folder."""
    relative = f"artifacts/{task}/{attempt}"
    folder = run / relative
    folder.mkdir(parents=True, exist_ok=True)
    checks = {"checks": [{"status": "fail", "name": "layout", "evidence": "box missing"}]}
    contents = {
        "grader.json": json.dumps({"observations": checks}),
        "stdout.log": "starting at http://127.0.0.1:4100\n" + json.dumps(checks) + "\n",
        "stderr.log": "warning from /Users/fixture/runner.py\n",
    }
    for name in EVIDENCE_FILES:
        (folder / name).write_text(contents.get(name, f"{name} of {task}\n"), encoding="utf-8")
    return relative


def write_tasks(runner: Path, task_ids: list[str]) -> dict[str, str]:
    digests = {}
    for index, task in enumerate(task_ids):
        definition = {
            "title": f"Synthetic task {index}",
            "description": "Reads http://127.0.0.1:4100/fixture",
            "subset_id": f"subset-{index % 3}",
            "features": ["layout"] if index % 2 else [],
            "scene": {"kind": "static"},
            "driver": {"script": "driver.js"},
            "grader": {"script": "grader.js"},
        }
        digests[task] = write_json(runner / "tasks" / f"{task}.json", definition)
    return digests


def write_primary_rounds(runs: Path, labels: dict, digests: dict, moli: str, manifest: str) -> list[str]:
    task_ids = list(digests)
    artifacts = []
    for name in ROUNDS:
        mode, phase = name.split("_")
        run = runs / labels[name]
        rows = []
        for index, task in enumerate(task_ids):
            for attempt in range(1, 4):
                status = attempt_status(mode, phase, index, attempt)
                row = {
                    "task_id": task,
                    "attempt": attempt,
                    "engine": "moli",
                    "seed": f"seed-{index}-{attempt}",
                    "status": status,
                    "duration_ms": 1000 + index * 3 + attempt * 7 + (50 if mode == "on" else 0),
                    "answer": f"Answer {index} from http://127.0.0.1:4100/answer",
                    "failure": None if status == "pass" else failure_record(index, attempt),
                    "artifact_dir": write_call_evidence(run, task, attempt)
                    if index < 3
                    else f"artifacts/{task}/{attempt}",
                    "engine_provenance": {"layout_enabled": mode == "on"},
                }
                if phase == "engine":
                    row["resource"] = {
                        "cpu_total_ms": 400 + index + attempt,
                        "cpu_user_ms": 300 + index,
                        "cpu_system_ms": 100 + attempt,
                        "pss_baseline_bytes": 1048576 * 10,
                        "pss_end_bytes": 1048576 * (20 + index % 5),
                        "pss_peak_bytes": 1048576 * (30 + index % 7),
                        "pss_peak_delta_bytes": 1048576 * (20 + index % 7),
                        "samples_seen": 4,
                        "quality_flags": ["short_call"] if index % 9 == 0 else [],
                        "measurement_backend": {"memory_pss": "proc_smaps_rollup"},
                        "unavailable": {"gpu": "absent"} if index % 13 == 0 else {},
                        "collection_wall_ms": 40 + index % 3,
                        "fixture_traffic": {
                            "fixture_app_rx_body_bytes": 2048 + index,
                            "fixture_app_tx_body_bytes": 512 + attempt,
                        },
                    }
                rows.append(row)
        write_jsonl(run / "results.jsonl", rows)
        write_json(
            run / "run_manifest.json",
            {
                "run_id": labels[name],
                "completion_status": "completed",
                "expected_result_rows": len(rows),
                "engines": {"moli": {"sha256": moli}},
                "bench_manifest": {"sha256": manifest},
                "resolved_tasks": [
                    {"task_id": task, "sha256": digest, "path": f"tasks/{task}.json"}
                    for task, digest in digests.items()
                ],
                "resource_profile": {
                    "mode": phase,
                    "engine_order": "balanced_rotation",
                    "engine_order_algorithm": "forward-reverse",
                },
                "seed": "frozen-seed",
                "k_runs": 3,
                "selected_engines": ["moli"],
                "score_mode": "independent",
                "score_eligible": False,
                "host": {"os": "Linux", "architecture": "aarch64"},
                "started_at": f"2026-01-0{ROUNDS.index(name) + 1}T00:00:00+00:00",
                "completed_at": f"2026-01-0{ROUNDS.index(name) + 1}T06:00:00+00:00",
                "runner": {
                    "jobs": 1,
                    "browser_reuse": "none",
                    "harness_pins": {"harness": "c" * 64},
                    "source": {"tree_sha256": "d" * 64},
                    "fixtures": {"tree_sha256": "e" * 64},
                },
            },
        )
        write_json(run / "host_summary.json", {"polluted": False, "round": name})
        write_json(runs / f"{labels[name]}_launch.json", {"host_busy_at_start": name == "on_engine"})
        artifacts.append(f"{labels[name]}/results.jsonl")
    return artifacts


def write_cold_starts(runs: Path, moli: str) -> None:
    for mode_index, mode in enumerate(("off", "on")):
        for index in range(1, 6):
            directory = runs / f"cold_{index:02d}_{mode}"
            write_json(directory / "run_manifest.json", {"engines": {"moli": {"sha256": moli}}})
            write_jsonl(
                directory / "cold_start.jsonl",
                [
                    {
                        "status": "ready",
                        "ready_ms": 900 + index * 10 + mode_index,
                        "launch_cpu_ms": 200 + index,
                        "launch_peak_pss_bytes": 1048576 * (40 + index),
                    }
                ],
            )


def write_host_observations(runs: Path) -> None:
    rows = [
        {
            "utc": f"2026-01-0{day}T0{hour}:00:00+00:00",
            "load": [1.5 + day + hour],
            "other_moli_test_processes": ["test"] if day == 2 and hour == 3 else [],
            "observer_version": 2 if day > 2 else 1,
        }
        for day in range(1, 5)
        for hour in (1, 3)
    ]
    write_jsonl(runs / "macos-observations.jsonl", rows)


def version_stat(value: float) -> dict:
    return {"n": 1045, "mean": value, "median": value - 1, "p95": value + 5}


def write_version_evidence(root: Path, task_ids: list[str]) -> None:
    version_runs = root / "version-runs"
    memory = {
        config: {
            scope: {
                "pss_peak_mib": version_stat(60.0 + offset),
                "cpu_time_ms": version_stat(500.0 - offset * 3),
                "unpublished_metric": version_stat(1.0),
            }
            for scope in ("all_predeclared_calls", "frozen_historical_common_pass_calls")
        }
        for offset, config in enumerate(("114_off", "118_off", "118_on"))
    }
    configs = {config: {"sha256": sha(config.encode())} for config in memory}
    comparison_sha = write_json(
        version_runs / "resource-comparison.json",
        {"memory": memory, "original_image_memory_status": "not_reproduced"},
    )
    protocol_sha = write_json(
        version_runs / "protocol.json", {"functional_tasks": task_ids, "configs": configs}
    )
    write_json(
        version_runs / "resource-certification.json",
        {
            "comparison_sha256": comparison_sha,
            "protocol_sha256": protocol_sha,
            "artifact_sha256": {"resource-comparison.json": comparison_sha},
        },
    )
    candidate = root / "version-candidate"
    write_json(candidate / "run_manifest.json", {"completion_status": "completed", "expected_result_rows": 3})
    write_jsonl(
        candidate / "results.jsonl",
        [
            {
                "task_id": CANDIDATE_114,
                "attempt": attempt,
                "status": "fail",
                "engine_provenance": {
                    "layout_enabled": False,
                    "binary_sha256": configs["114_off"]["sha256"],
                },
            }
            for attempt in range(1, 4)
        ],
    )


def write_functional_correction(directory: Path) -> None:
    configurations = []
    for config in ("114_off", "118_off", "118_on"):
        run = directory / f"retest_{config}"
        configurations.append(
            {
                "id": config,
                "run_id": run.name,
                "rows": 6,
                "results_sha256": write_jsonl(run / "results.jsonl", [{"config": config}]),
                "manifest_sha256": write_json(run / "run_manifest.json", {"config": config}),
                "outcomes": {"first": {"attempts": ["fail"] * 3}, "second": {"attempts": ["fail"] * 3}},
            }
        )
    write_json(
        directory / "receipt.json",
        {
            "schema": "moli_functional_contract_correction/1",
            "effect": {"report_success_rates_changed": False},
            "configurations": configurations,
            "tasks": ["first", "second"],
            "harness_commit": "f" * 40,
        },
    )


def write_fixed_summaries(root: Path, policy_root: Path, digests: dict, binaries: dict) -> list[str]:
    """Write one complete candidate per method and return its command-line arguments."""
    arguments = []
    variant = list(digests)[2]
    variant_digest = write_json(
        policy_root / "tasks" / f"{variant}.json",
        {"title": "Revised synthetic task", "description": "Reads http://127.0.0.1:4100/revised"},
    )
    tasks = {**digests, variant: variant_digest}
    population = {
        "cases": len(digests),
        "attempts_per_case": 3,
        "calls": len(digests) * 3,
        "task_ids_sha256": sha("".join(task + "\n" for task in sorted(digests)).encode()),
    }
    for method, binary in binaries.items():
        layout = method.rsplit("_", 1)[1]
        run_id = f"policy_{method}"
        run = policy_root / "runs" / run_id
        cases = []
        rows = []
        for index, (task, digest) in enumerate(tasks.items()):
            attempts = []
            for attempt in range(1, 4):
                status = attempt_status(layout, "baseline", index, attempt)
                row = {
                    "attempt": attempt,
                    "status": status,
                    "source_run_id": run_id,
                    "answer": f"Fixed answer {index}",
                    "failure": None if status == "pass" else failure_record(index, attempt),
                    "artifact_dir": write_call_evidence(run, task, attempt)
                    if index < 2
                    else f"artifacts/{task}/{attempt}",
                }
                attempts.append(row)
                rows.append({"task_id": task, **row})
            cases.append(
                {
                    "task_id": task,
                    "task_sha256": digest,
                    "passes": sum(row["status"] == "pass" for row in attempts),
                    "attempts": attempts,
                }
            )
        passed = sum(case["passes"] == 3 for case in cases)
        summary = {
            "schema": "lexbench_moli_fixed_layout_summary/1",
            "layout": layout,
            "population": population,
            "cases": cases,
            "outcomes": {"passed_cases": passed, "success_rate_pct": passed / 372 * 100},
            "binary": {"moli_sha256": binary},
            "sources": [
                {
                    "run_id": run_id,
                    "calls": 1116,
                    "results_file": "results.jsonl",
                    "results_sha256": write_jsonl(run / "results.jsonl", rows),
                    "manifest_sha256": write_json(
                        run / "run_manifest.json",
                        {
                            "resolved_tasks": [
                                {"task_id": task, "sha256": digest, "path": f"tasks/{task}.json"}
                                for task, digest in tasks.items()
                            ]
                        },
                    ),
                }
            ],
        }
        path = root / "fixed-summaries" / f"{method}.json"
        write_json(path, summary)
        arguments += ["--fixed-summary", method, str(path)]
    return arguments


def resource_builds(target: str, *, distinct: bool) -> dict:
    return {
        method: {
            "sha256": sha((method if distinct else method.rsplit("_", 1)[0]).encode()),
            "layout": method.rsplit("_", 1)[1],
            "source_commit": "a" * 40,
            "profile": "release",
            "target": target,
        }
        for method in ("119_official_off", "119_official_on", "119_off", "119_on")
    }


def write_macos_resources(root: Path, builds: dict) -> list[str]:
    """Write a complete matched matrix plus the two macOS summaries it certifies."""
    matrix = root / "resource-matrix"
    runner = "macos-runner"
    runner_root = root / runner
    orchestrator = b"# frozen synthetic orchestrator\n"
    matrix.mkdir(parents=True)
    (matrix / "orchestrator.py").write_bytes(orchestrator)
    methods = {
        method: {**build, "run_id": f"resource_{method}", "binary": f"/opt/{method}"}
        for method, build in builds.items()
    }
    protocol = {
        "schema": "moli_resource_matrix_protocol/1",
        "selected_round": 1,
        "task_ids": RESOURCE_TASKS,
        "attempts": 5,
        "sampler_interval_ms": 250,
        "methods": methods,
        "orchestrator_sha256": sha(orchestrator),
        "reference_protocol_sha256": "9" * 64,
        "runner_root": runner,
        "chart_keys": CHART_KEYS,
    }
    protocol_sha = write_json(matrix / "protocol.json", protocol)
    row_hashes = {}
    results = {}
    measured = {}
    for offset, method in enumerate(methods):
        run = runner_root / "runs" / methods[method]["run_id"]
        rows = [
            {
                "task_id": task,
                "attempt": attempt,
                "engine_provenance": {"pid": 1000 + index},
                "resource": {
                    "rss_peak_bytes": 1048576 * (70 + offset * 2 + index % 11),
                    "cpu_total_ms": 450 + offset * 5 + index % 17 + attempt,
                    "measurement_backend": {"memory_rss": "darwin_ps_process_tree_rss"},
                    "process_count_peak": 3,
                },
            }
            for index, task in enumerate(RESOURCE_TASKS)
            for attempt in range(1, 6)
        ]
        measured[method] = {(row["task_id"], row["attempt"]): row for row in rows}
        for row in rows:
            row_hashes[method, row["task_id"], row["attempt"]] = sha(
                json.dumps(row, sort_keys=True).encode()
            )
        results[method] = {
            "run_manifest.json": write_json(run / "run_manifest.json", {"run_id": run.name}),
            "results.jsonl": write_jsonl(run / "results.jsonl", rows),
            "host_summary.json": write_json(run / "host_summary.json", {"method": method}),
        }
    calls = [
        {
            "method": method,
            "task_id": task,
            "attempt": attempt,
            "started_ns": position * 10,
            "ended_ns": position * 10 + 5,
            "binary_sha256": builds[method]["sha256"],
            "row_sha256": row_hashes[method, task, attempt],
        }
        for position, (method, task, attempt) in enumerate(schedule(RESOURCE_TASKS, 5, list(methods)))
    ]
    write_json(
        matrix / "receipt.json",
        {
            "schema": "moli_resource_matrix_receipt/1",
            "selected_round": 1,
            "physical_calls": len(calls),
            "results": results,
            "orchestrator_sha256": sha(orchestrator),
            "protocol_sha256": protocol_sha,
            "physical_calls_sha256": write_jsonl(matrix / "physical-calls.jsonl", calls),
        },
    )
    chart = sorted((key["task_id"], key["attempt"]) for key in CHART_KEYS)
    arguments = []
    for option, name, family in (
        ("--official-resource-summary", "official", "119_official"),
        ("--macos-resource-summary", "patched", "119"),
    ):
        configurations = {}
        for layout in ("off", "on"):
            method = f"{family}_{layout}"
            rows = measured[method]
            metrics = {}
            for scope, keys in (("all_predeclared_calls", sorted(rows)), ("frozen_historical_common_pass_calls", chart)):
                metrics[scope] = {
                    "rss_peak_mib": stats([rows[key]["resource"]["rss_peak_bytes"] / 1024**2 for key in keys]),
                    "cpu_time_ms": stats([rows[key]["resource"]["cpu_total_ms"] for key in keys]),
                }
            configurations[layout] = {
                "metrics": metrics,
                "provenance": {
                    "profiled": {
                        "run_id": methods[method]["run_id"],
                        "manifest_sha256": results[method]["run_manifest.json"],
                        "results_sha256": results[method]["results.jsonl"],
                        "host_summary_sha256": results[method]["host_summary.json"],
                    }
                },
            }
        path = root / f"{name}-macos-summary.json"
        write_json(
            path,
            {
                "schema": "lexbench_moli_macos_fixed_resource_summary/1",
                "method": {
                    "configurations": ["layout_off", "layout_on"],
                    "layout_retry": False,
                    "resource_runs": 2,
                },
                "population": {
                    "tasks": 557,
                    "attempts_per_task": 5,
                    "calls_per_configuration": 2785,
                    "frozen_comparison_calls": 1045,
                    "frozen_comparison_tasks": 209,
                },
                "quality": {
                    "memory_metric": "process_tree_rss",
                    "pss_available": False,
                    "publishable": True,
                },
                "configurations": configurations,
                "candidate": {
                    "binary_sha256": builds[f"{family}_off"]["sha256"],
                    "source_commit": builds[f"{family}_off"]["source_commit"],
                    "profile": "release",
                    "target": builds[f"{family}_off"]["target"],
                },
                "provenance": {"comparison_protocol_sha256": protocol["reference_protocol_sha256"]},
            },
        )
        arguments += [option, str(path)]
    return arguments + ["--resource-matrix", str(matrix)]


def write_linux_profiles(root: Path, builds: dict) -> list[str]:
    directory = root / "linux-profiles"
    runner = "linux-runner"
    (root / runner).mkdir(parents=True)
    (root / runner / "resources.py").write_text("# frozen collector\n", encoding="utf-8")
    directory.mkdir()
    orchestrator = b"# frozen synthetic collector\n"
    (directory / "orchestrator.py").write_bytes(orchestrator)
    protocol = {
        "schema": "moli_linux_resource_profiles/1",
        "memory_metric": "process_tree_pss",
        "attempts": 5,
        "jobs": 1,
        "task_ids": RESOURCE_TASKS,
        "configs": builds,
        "max_observer_effect_pct": 10,
        "runner_root": runner,
        "orchestrator_sha256": sha(orchestrator),
        "input_files": {"resources.py": sha((root / runner / "resources.py").read_bytes())},
        "chart_keys": CHART_KEYS,
    }
    results = {}
    measured, baseline, after, before = experiment(count=557)
    for offset, (method, build) in enumerate(builds.items()):
        for phase, samples, manifest in (("baseline", baseline, before), ("engine", measured, after)):
            manifest.update(completion_status="completed", k_runs=5)
            manifest["engines"] = {"first": {"sha256": build["sha256"]}}
            manifest["resource_profile"]["mode"] = phase
            run = directory / f"{method}_{phase}"
            rows = [
                dict(
                    sample,
                    attempt=attempt,
                    resource={
                        "collection_wall_ms": 108,
                        "pss_peak_bytes": 1048576 * (50 + offset + index % 13),
                        "cpu_total_ms": 300 + offset * 7 + index % 19 + attempt,
                        "samples_seen": 3,
                        "quality_flags": [],
                        "sample_interval_ms": 250,
                        "measurement_backend": {"memory_pss": "proc_smaps_rollup", "cpu": "proc_tree"},
                    },
                    engine_provenance={
                        "binary_sha256": build["sha256"],
                        "layout_enabled": build["layout"] == "on",
                        "launch_command": ["fixture"] + (["--layout"] if build["layout"] == "on" else []),
                    },
                )
                for attempt in range(1, 6)
                for index, sample in enumerate(samples)
            ]
            results[f"{method}:{phase}"] = {
                "run_id": run.name,
                "rows": len(rows),
                "results_sha256": write_jsonl(run / "results.jsonl", rows),
                "run_manifest_sha256": write_json(run / "run_manifest.json", manifest),
                "host_summary_sha256": write_json(run / "host_summary.json", {"polluted": False}),
            }
    write_json(
        directory / "receipt.json",
        {
            "schema": "moli_linux_resource_profiles_receipt/1",
            "protocol_sha256": write_json(directory / "protocol.json", protocol),
            "orchestrator_sha256": sha(orchestrator),
            "results": results,
        },
    )
    return ["--linux-resource-profiles", str(directory)]


def write_vendor_assets(directory: Path) -> None:
    directory.mkdir(parents=True)
    for name, (content, _) in VENDOR_ASSETS.items():
        (directory / name).write_bytes(content)


def build_report_inputs(root: Path, scenario: str) -> list[str]:
    """Write every retained input of one scenario and return its command-line arguments.

    The caller runs the report from ``root`` because the resource protocols name
    their collector roots relatively.
    """
    runs = root / "evidence"
    runner = root / "runner"
    policy_root = root / "layout-policy-runner"
    size = 5 if scenario == "minimal" else 372
    task_ids = [CANDIDATE_114, *(f"task-{index:03d}" for index in range(1, size))]
    moli, manifest = sha(b"moli 1.1.8"), sha(b"bench manifest")
    digests = write_tasks(runner, task_ids)
    labels = {name: f"round_{name}" for name in ROUNDS}
    cohort = {
        "k_runs": 3,
        "moli_sha256": moli,
        "manifest_sha256": manifest,
        "selection": {"rule": "frozen historical failures"},
        "strata": {
            stratum: [
                {"task_id": task, "sha256": digests[task]}
                for index, task in enumerate(task_ids)
                if index % 2 == position
            ]
            for position, stratum in enumerate(("beta", "alpha"))
        },
    }
    write_json(runs / "report-cohort.json", cohort)
    write_json(runs / "report-runs.json", {"runs": labels})
    write_json(runs / "environment.json", {"docker": "synthetic", "root": "/tmp/synthetic"})
    artifacts = write_primary_rounds(runs, labels, digests, moli, manifest)
    write_cold_starts(runs, moli)
    if scenario != "minimal":
        write_host_observations(runs)
    protocol_sha = write_json(
        runs / "protocol.json",
        {
            "task_ids": task_ids,
            "tasks": {task: {"sha256": digest} for task, digest in digests.items()},
            "try_layout": False,
        },
    )
    write_json(
        runs / "certification.json",
        {
            "protocol_sha256": protocol_sha,
            "artifact_sha256": {path: sha((runs / path).read_bytes()) for path in artifacts},
        },
    )
    write_version_evidence(root, task_ids)
    write_functional_correction(root / "correction")
    write_vendor_assets(root / "vendor")
    arguments = []
    builds = {}
    if scenario != "minimal":
        builds = resource_builds(
            "aarch64-apple-darwin" if scenario == "macos" else "aarch64-unknown-linux-gnu",
            distinct=scenario == "linux",
        )
        binaries = {method: sha(f"functional {method}".encode()) for method in builds}
        arguments += write_fixed_summaries(root, policy_root, digests, binaries)
        arguments += (write_macos_resources if scenario == "macos" else write_linux_profiles)(root, builds)
        arguments.append("--include-english")
    profile = root / "report-profile.json"
    write_json(
        profile,
        {
            "functional_population": {"cases": size, "attempts_per_case": 3},
            "resource_population": {
                "tasks": 557,
                "attempts_per_task": 5,
                "chart_tasks": 209,
                "chart_calls": 1045,
            },
            "resource_builds": builds,
            "paths": {
                "cohort": str(runs / "report-cohort.json"),
                "output": str(root / "report"),
                "vendor_dir": str(root / "vendor"),
                "functional_correction": str(root / "correction"),
                "runner": str(runner),
                "version_runs": str(root / "version-runs"),
                "version_candidate": str(root / "version-candidate"),
                "layout_policy_runner": str(policy_root),
            },
        },
    )
    return ["--profile", str(profile), *arguments]
