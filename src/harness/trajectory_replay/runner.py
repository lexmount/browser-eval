"""Docker-isolated browser trajectory replay runner."""

from __future__ import annotations

import hashlib
import threading
import time
import traceback
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import docker
from playwright.sync_api import Page, sync_playwright

from harness.environments.browser import (
    cleanup_browser_runtime,
    configured_browser_images,
    rotated_engine_order,
)
from harness.resource_accounting import DockerStatsResources, ResourceSampler
from harness.trajectory_replay.core.diagnostics import diagnose_summary
from harness.trajectory_replay.core.docker_runner import (
    EngineRuntime,
    attempt_identity,
    docker_environment,
    ground_truth_validation,
    http_origin,
    main_frame_origin_recorder,
    manifest_task,
    materialize_task,
    open_task_page,
    output_volume,
    print_attempt_progress,
    process_validation,
    reset_browser_state,
    resource_result,
    start_engine,
    start_fixture_server,
    stop_fixture_server,
    write_run_artifacts,
)
from harness.trajectory_replay.core.executor import judge_outputs, validate_outputs
from harness.trajectory_replay.core.suite import BrowserTask, validate_ground_truth_evidence
from harness.trajectory_replay.executor import execute_steps
from harness.trajectory_replay.metrics import summarize_attempts
from harness.trajectory_replay.participants import DEFAULT_DIAGNOSIS_MODEL
from harness.trajectory_replay.report import write_latest_report
from harness.trajectory_replay.suite import load_suite
from harness.web_fetch.safety import unlinked_path

ENGINE_IMAGES = configured_browser_images()


def _reset_state(page: Page, visited_origins: set[str]) -> None:
    origins = set(visited_origins)
    current_origin = http_origin(page.url)
    if current_origin:
        origins.add(current_origin)
    reset_browser_state(page, origins)


def _prepare_page(page: Page, visited_origins: set[str], *, settle_ms: int) -> Page:
    """Reset task state and replace only the renderer page, keeping the browser/context warm."""
    context = page.context
    _reset_state(page, visited_origins)
    visited_origins.clear()
    page.close(run_before_unload=False)
    return open_task_page(context, {"width": 1280, "height": 900}, settle_ms=settle_ms)


def _run_task(
    engine: str,
    page: Page,
    visited_origins: set[str],
    resources: DockerStatsResources,
    task: BrowserTask,
    attempt: int,
    sample_interval_ms: int,
    *,
    is_retry: bool = False,
) -> dict[str, Any]:
    record_origin = main_frame_origin_recorder(page, visited_origins)
    page.on("framenavigated", record_origin)
    before = resources.counters()
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    sampler.start()
    started = time.perf_counter()
    outputs: dict[str, str] = {}
    trace: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    error = ""
    try:
        execute_steps(page, task.tool_calls, trace=trace, outputs=outputs)
        validation = judge_outputs(outputs, task.assertions)
        failures = validate_outputs(outputs, task.assertions)
        status = "pass" if not failures else "fail"
        error = "; ".join(failures)
        trace.append(
            {
                "index": len(task.tool_calls) + 1,
                "action": "validate_assertions",
                "arguments": {"assertions": list(task.assertions)},
                "status": status,
                "duration_ms": 0.0,
                "output": "all assertions passed" if not failures else error,
                "checks": validation,
            }
        )
    except Exception:
        status = "fail"
        error = traceback.format_exc()
        validation = judge_outputs(outputs, task.assertions)
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        sampler.stop()
        page.remove_listener("framenavigated", record_origin)
    after = resources.counters()
    process_check = process_validation(task, trace)
    process_passed = process_check["passed"]
    result_passed = bool(validation) and all(item.get("passed") is True for item in validation)
    return {
        **attempt_identity(engine, task, attempt, is_retry=is_retry),
        "status": status,
        "result_status": "pass" if result_passed else "fail",
        "process_status": "pass" if process_passed else "fail",
        "combined_status": "pass" if result_passed and process_passed else "fail",
        "duration_ms": round(duration_ms, 3),
        **output_volume(outputs),
        "error": error,
        "trace": trace,
        "validation": validation,
        "process_validation": process_check,
        "resource": resource_result(sampler, before, after, duration_ms),
    }


def run_evaluation(
    *,
    suite_path: str | Path,
    output_dir: str | Path,
    reports_dir: str | Path,
    ground_truth_evidence_path: str | Path | None = None,
    agent_smoke_path: str | Path | None = None,
    max_retries: int = 1,
    sample_interval_ms: int = 50,
    settle_ms: int = 1500,
    cpus: float = 2.0,
    memory: str = "2g",
    llm_diagnostics: bool = False,
    diagnosis_model: str = DEFAULT_DIAGNOSIS_MODEL,
    diagnosis_reasoning_effort: str = "low",
    generate_report: bool = True,
) -> dict[str, Any]:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    output = unlinked_path(Path(output_dir))
    if output.exists():
        raise ValueError("Output must be a new directory; existing evidence is immutable")
    from harness.trajectory_replay.report_inputs import (
        agent_smoke_input,
        replay_fixture_inputs,
        replay_input_path,
        replay_suite_binding,
    )
    suite_path = replay_input_path(suite_path, current='curated')
    suite = load_suite(suite_path)
    smoke = agent_smoke_input(agent_smoke_path)
    fixture_root, fixture_hashes = replay_fixture_inputs(
        Path(suite_path), (call for task in suite.tasks for call in task.tool_calls)
    )
    uses_local_fixtures = fixture_root is not None
    ground_truth_evidence = (
        validate_ground_truth_evidence(suite_path, suite, ground_truth_evidence_path)
        if ground_truth_evidence_path is not None
        else None
    )
    unlinked_path(output)
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ValueError(
            "Output must be a new directory; existing evidence is immutable"
        ) from error
    run_id = uuid.uuid4().hex[:12]
    client = docker.from_env()
    runtime = EngineRuntime()
    rows: list[dict[str, Any]] = []
    playwright = sync_playwright().start()
    fixture_runtime: tuple[ThreadingHTTPServer, threading.Thread, str] | None = None
    if uses_local_fixtures:
        fixture_runtime = start_fixture_server(fixture_root)
    fixture_origin = fixture_runtime[2] if fixture_runtime else ""
    try:
        for engine in ENGINE_IMAGES:
            start_engine(
                runtime,
                client,
                playwright,
                engine=engine,
                image=ENGINE_IMAGES[engine],
                run_id=run_id,
                cpus=cpus,
                memory=memory,
                reset_state=_reset_state,
            )

        for task_index, declared_task in enumerate(suite.tasks):
            task = materialize_task(declared_task, fixture_origin)
            for engine in rotated_engine_order(tuple(ENGINE_IMAGES), task_index):
                for attempt in range(1, max_retries + 2):
                    runtime.pages[engine] = _prepare_page(
                        runtime.pages[engine],
                        runtime.visited_origins[engine],
                        settle_ms=settle_ms,
                    )
                    row = _run_task(
                        engine,
                        runtime.pages[engine],
                        runtime.visited_origins[engine],
                        runtime.resources[engine],
                        task,
                        attempt,
                        sample_interval_ms,
                        is_retry=attempt > 1,
                    )
                    row["run_id"] = run_id
                    rows.append(row)
                    print_attempt_progress(row)
                    if row["status"] == "pass":
                        break
        summary = summarize_attempts(rows, engines=tuple(ENGINE_IMAGES))
        manifest = {
            "schema": "lex_browser_use_manifest/2",
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fixture_files": fixture_hashes,
            "suite_sha256": hashlib.sha256(Path(suite_path).read_bytes()).hexdigest(),
            "suite_dataset": replay_suite_binding(
                hashlib.sha256(Path(suite_path).read_bytes()).hexdigest(),
                family="curated",
            ),
            "suite": [manifest_task(task) for task in suite.tasks],
            "ground_truth_validation": ground_truth_validation(ground_truth_evidence),
            "protocol": {
                "browser_reuse": True,
                "viewport": {"width": 1280, "height": 900},
                "viewport_verified_before_each_task": True,
                "step_definition": (
                    "one declared CowAgent BrowserTool-equivalent call; implicit waits excluded"
                ),
                "llm_scope": ("LLM never judges success; optional LLM only explains failed traces"),
                "llm_failure_diagnostics": llm_diagnostics,
                "diagnosis_model": diagnosis_model if llm_diagnostics else None,
                "diagnosis_reasoning_effort": (
                    diagnosis_reasoning_effort if llm_diagnostics else None
                ),
                "cold_start_excluded": True,
                "state_reset_between_tasks": True,
                "state_reset_method": (
                    "warm browser/context; clear all top-level navigation origins, "
                    "browser cache, cookies, permissions and history; replace renderer page"
                ),
                "sample_interval_ms": sample_interval_ms,
                "post_reset_settle_ms": settle_ms,
                "primary_attempts_per_task": 1,
                "max_failure_retries": max_retries,
                "engines": list(ENGINE_IMAGES),
                "engine_order": "balanced by task index",
                "target_mode": "public_web" if not uses_local_fixtures else "mixed",
                "fixture_origin": (
                    "temporary self-hosted deterministic fixture server"
                    if uses_local_fixtures
                    else None
                ),
                "resource_scope": "browser container cgroup/network namespace",
                "controller_excluded": True,
                "cpus_per_browser": cpus,
                "memory_per_browser": memory,
                "network_isolation": "one dedicated bridge with no peer containers per engine",
            },
            "environment": docker_environment(client, runtime.containers),
        }
        if smoke is not None:
            manifest["agent_smoke"] = smoke
        # Persist core evidence before optional remote diagnostics can fail or stall.
        write_run_artifacts(output, rows, summary, manifest)
        diagnose_summary(
            summary,
            use_llm=llm_diagnostics,
            model=diagnosis_model,
            reasoning_effort=diagnosis_reasoning_effort,
        )
        write_run_artifacts(output, rows, summary, manifest)
        if generate_report:
            write_latest_report(reports_dir, manifest=manifest, summary=summary)
        return summary
    finally:
        if fixture_runtime:
            stop_fixture_server(fixture_runtime)
        cleanup_browser_runtime(
            runtime.browsers, playwright, runtime.containers, runtime.networks
        )
