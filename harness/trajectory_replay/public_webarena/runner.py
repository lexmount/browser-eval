"""Docker-isolated browser use evaluation runner."""

from __future__ import annotations

import hashlib
import json
import platform
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import docker
import tiktoken
from playwright.sync_api import Browser, Page, sync_playwright

from harness.container_isolation import require_linux_cgroup_v2
from harness.environments.browser import (
    DEFAULT_BROWSER_IMAGES,
    ENGINE_PORTS,
    cleanup_browser_runtime,
    published_endpoint,
    start_browser_container,
    validate_resource_controls,
    wait_cdp,
)
from harness.resource_accounting import (
    DockerStatsResources,
    ResourceSampler,
    delta_counters,
)

from .diagnostics import diagnose_summary
from .evaluators import EvaluatorRegistry, TaskEvidence, write_agent_response
from .executor import execute_steps
from .har import CdpHarRecorder
from .metrics import summarize_attempts
from .qualification import task_contract_sha256
from .report import write_latest_report
from .response_builder import build_agent_response
from .suite import BrowserTask, load_suite, validate_ground_truth_evidence
from .webarena_environment import WebArenaEnvironment, wall_clock_timeout

ENGINE_IMAGES = dict(DEFAULT_BROWSER_IMAGES)
ENCODING = tiktoken.get_encoding("o200k_base")


def _remaining_task_budget_ms(deadline: float, *, phase: str) -> int:
    remaining_ms = int((deadline - time.perf_counter()) * 1000)
    if remaining_ms < 1:
        raise TimeoutError(f"task wall-clock budget exhausted before {phase}")
    return remaining_ms






def _http_origin(url: str) -> str | None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _reset_state(page: Page, visited_origins: set[str]) -> None:
    context = page.context
    origins = set(visited_origins)
    for existing in list(getattr(context, "pages", [page])):
        current_origin = _http_origin(existing.url)
        if current_origin:
            origins.add(current_origin)
    try:
        page.evaluate(
            """async () => {
              try { localStorage.clear(); } catch (_) {}
              try { sessionStorage.clear(); } catch (_) {}
              try { for (const key of await caches.keys()) await caches.delete(key); } catch (_) {}
              try {
                for (const item of await navigator.serviceWorker.getRegistrations())
                  await item.unregister();
              } catch (_) {}
              try {
                for (const item of await indexedDB.databases())
                  indexedDB.deleteDatabase(item.name);
              } catch (_) {}
            }"""
        )
    except Exception:
        pass
    context.clear_cookies()
    context.clear_permissions()
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.clearBrowserCookies")
        for origin in sorted(origins):
            try:
                cdp.send(
                    "Storage.clearDataForOrigin",
                    {"origin": origin, "storageTypes": "all"},
                )
            except Exception:
                pass
        page.goto("about:blank", wait_until="load", timeout=10_000)
        try:
            cdp.send("Page.resetNavigationHistory")
            try:
                cdp.send("HeapProfiler.enable")
                cdp.send("HeapProfiler.collectGarbage")
                cdp.send("HeapProfiler.disable")
            except Exception:
                pass
        finally:
            cdp.detach()
    except Exception:
        page.goto("about:blank", wait_until="load", timeout=10_000)


def _resource_result(
    source: DockerStatsResources,
    sampler: ResourceSampler,
    before: Any,
    after: Any,
    duration_ms: float,
) -> dict[str, int | float]:
    result = {**delta_counters(before, after), **sampler.summary()}
    if duration_ms > 0:
        result["cpu_average_utilization_pct"] = result["cpu_ms"] / duration_ms * 100
        result["network_rx_bytes_per_second"] = result["network_rx_bytes"] / duration_ms * 1000
        result["network_tx_bytes_per_second"] = result["network_tx_bytes"] / duration_ms * 1000
    return result


def _capture_final_page_evidence(page: Page, artifact_dir: Path) -> dict[str, Any]:
    """Persist post-HAR visual/page evidence and fail closed when it is unavailable."""
    screenshot_path = artifact_dir / "final.png"
    page_path = artifact_dir / "final-page.json"
    candidates = [candidate for candidate in page.context.pages if not candidate.is_closed()]
    capture_page = candidates[-1] if candidates else page
    capture_errors: list[dict[str, str]] = []
    try:
        capture_page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except Exception as exc:
        capture_errors.append({"stage": "load_state", "error": type(exc).__name__})

    screenshot_bytes = b""
    for _ in range(3):
        try:
            screenshot_bytes = capture_page.screenshot(
                path=str(screenshot_path), full_page=False, timeout=5_000
            )
            if not screenshot_bytes:
                screenshot_bytes = screenshot_path.read_bytes()
            break
        except Exception as exc:
            capture_errors.append({"stage": "screenshot", "error": type(exc).__name__})
            capture_page.wait_for_timeout(250)

    dom_evidence = None
    for _ in range(3):
        try:
            dom_evidence = capture_page.evaluate(
                r"""() => {
          const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
          const candidates = Array.from(document.querySelectorAll(
            'a, button, input, select, textarea, [role], [contenteditable="true"], [tabindex]'
          ));
          return {
            visibleText: normalize(document.body ? document.body.innerText : '').slice(0, 40000),
            interactiveElements: candidates.slice(0, 1000).map((element, index) => ({
              index,
              tag: element.tagName.toLowerCase(),
              role: element.getAttribute('role'),
              text: normalize(element.innerText || element.textContent).slice(0, 500),
              id: element.id || null,
              name: element.getAttribute('name'),
              ariaLabel: element.getAttribute('aria-label'),
              placeholder: element.getAttribute('placeholder'),
              title: element.getAttribute('title'),
              href: element.getAttribute('href'),
              type: element.getAttribute('type'),
              checked: 'checked' in element ? Boolean(element.checked) : null,
              selected: 'selected' in element ? Boolean(element.selected) : null,
              disabled: 'disabled' in element ? Boolean(element.disabled) : null,
              visible: Boolean(element.getClientRects().length),
            })),
          };
        }"""
            )
            break
        except Exception as exc:
            capture_errors.append({"stage": "dom", "error": type(exc).__name__})
            capture_page.wait_for_timeout(250)
    dom_captured = dom_evidence is not None
    if dom_evidence is None:
        dom_evidence = {"visibleText": "", "interactiveElements": []}

    open_pages = []
    for candidate in list(page.context.pages):
        try:
            open_pages.append({"url": str(candidate.url), "title": candidate.title()})
        except Exception as exc:
            open_pages.append(
                {
                    "url": str(getattr(candidate, "url", "")),
                    "title_error": type(exc).__name__,
                }
            )
    try:
        title = capture_page.title()
    except Exception as exc:
        title = ""
        capture_errors.append({"stage": "title", "error": type(exc).__name__})
    try:
        viewport = capture_page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    except Exception as exc:
        viewport = None
        capture_errors.append({"stage": "viewport", "error": type(exc).__name__})
    page_payload = {
        "url": str(capture_page.url),
        "title": title,
        "visible_text": dom_evidence["visibleText"],
        "interactive_elements": dom_evidence["interactiveElements"],
        "open_pages": open_pages,
        "viewport": viewport,
        "capture_errors": capture_errors,
    }
    page_bytes = (json.dumps(page_payload, indent=2, ensure_ascii=False) + "\n").encode()
    page_path.write_bytes(page_bytes)
    complete = bool(screenshot_bytes and dom_captured and viewport)
    return {
        "status": "complete" if complete else "partial",
        "screenshot": (
            {
                "path": screenshot_path.name,
                "sha256": hashlib.sha256(screenshot_bytes).hexdigest(),
                "bytes": len(screenshot_bytes),
            }
            if screenshot_bytes
            else None
        ),
        "page": {
            "path": page_path.name,
            "sha256": hashlib.sha256(page_bytes).hexdigest(),
            "bytes": len(page_bytes),
        },
        "capture_errors": capture_errors,
    }




def _prepare_page(
    page: Page,
    visited_origins: set[str],
    *,
    settle_ms: int,
    viewport: dict[str, int] | None = None,
) -> Page:
    """Reset task state and replace only the renderer page, keeping the browser/context warm."""
    context = page.context
    _reset_state(page, visited_origins)
    visited_origins.clear()
    for existing in list(getattr(context, "pages", [page])):
        existing.close(run_before_unload=False)
    replacement = context.new_page()
    expected_viewport = viewport or {"width": 1280, "height": 900}
    replacement.set_viewport_size(expected_viewport)
    viewport = replacement.evaluate("() => ({width: innerWidth, height: innerHeight})")
    if viewport != expected_viewport:
        raise RuntimeError(f"browser viewport mismatch: {viewport}")
    replacement.wait_for_timeout(max(0, settle_ms))
    return replacement


def _task_viewport(task: BrowserTask) -> dict[str, int]:
    if (task.environment or {}).get("kind") == "webarena_verified":
        return {"width": 1280, "height": 720}
    return {"width": 1280, "height": 900}


def _may_reuse_webarena_site(
    task: BrowserTask, initialized_sites: set[str]
) -> bool:
    sites = set((task.environment or {}).get("sites") or [])
    return bool(sites) and "mutate" not in task.tags and sites <= initialized_sites


def _engine_site_state(engines: tuple[str, ...]) -> dict[str, set[str]]:
    """Keep reset/reuse state independent for every measured browser engine."""
    return {engine: set() for engine in engines}


def _run_task(
    engine: str,
    page: Page,
    visited_origins: set[str],
    resources: DockerStatsResources,
    task: BrowserTask,
    attempt: int,
    sample_interval_ms: int,
    evaluator_registry: EvaluatorRegistry,
    artifact_dir: Path,
    *,
    is_retry: bool = False,
    task_timeout_ms: int = 300_000,
) -> dict[str, Any]:
    def record_origin(frame: Any) -> None:
        if frame != page.main_frame:
            return
        origin = _http_origin(str(frame.url))
        if origin:
            visited_origins.add(origin)

    page.on("framenavigated", record_origin)
    before = resources.counters()
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    sampler.start()
    started = time.perf_counter()
    task_deadline = started + task_timeout_ms / 1000
    outputs: dict[str, str] = {}
    trace: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    error = ""
    recorder: CdpHarRecorder | None = None
    execution_status = "fail"
    try:
        execution_budget_ms = _remaining_task_budget_ms(
            task_deadline, phase="trajectory execution"
        )
        recorder = CdpHarRecorder(page)
        execute_steps(
            page,
            task.tool_calls,
            trace=trace,
            outputs=outputs,
            page_observer=recorder.attach,
            task_timeout_ms=execution_budget_ms,
        )
        execution_status = "pass"
    except Exception:
        error = traceback.format_exc()
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        sampler.stop()
        page.remove_listener("framenavigated", record_origin)
    after = resources.counters()
    expected_actions = list(task.ground_truth.get("expected_actions") or [])
    tool_trace = [item for item in trace if item.get("action") != "validate_assertions"]
    actual_actions = [str(item.get("action") or "") for item in tool_trace]
    process_passed = bool(tool_trace) and actual_actions == expected_actions and all(
        item.get("status") == "pass" for item in tool_trace
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    har_path = artifact_dir / "network.har"
    try:
        if recorder is None:
            raise RuntimeError("network recorder did not start")
        har_summary = recorder.stop(har_path)
    except Exception:
        har_summary = {"status": "error"}
        error = f"{error}\n{traceback.format_exc()}".strip()
    try:
        evidence_budget_ms = _remaining_task_budget_ms(
            task_deadline, phase="final evidence capture"
        )
        with wall_clock_timeout(
            evidence_budget_ms / 1000, operation="final evidence capture"
        ):
            final_page_evidence = _capture_final_page_evidence(page, artifact_dir)
    except Exception:
        final_page_evidence = {"status": "error"}
        error = f"{error}\n{traceback.format_exc()}".strip()
    try:
        evaluator_budget_ms = _remaining_task_budget_ms(
            task_deadline, phase="result evaluation"
        )
        with wall_clock_timeout(evaluator_budget_ms / 1000, operation="result evaluation"):
            agent_response = build_agent_response(task, outputs)
            if agent_response is not None:
                write_agent_response(artifact_dir / "agent_response.json", agent_response)
            evaluation = evaluator_registry.evaluate(
                task,
                TaskEvidence(
                    outputs=outputs,
                    process_passed=process_passed,
                    agent_response=agent_response,
                    network_har_path=har_path if har_path.is_file() else None,
                ),
            )
        validation = list(evaluation.checks)
        result_passed = evaluation.passed
        if evaluation.error:
            error = "; ".join(part for part in (error, evaluation.error) if part)
    except Exception:
        result_passed = False
        error = f"{error}\n{traceback.format_exc()}".strip()
        evaluation = None
    evidence_passed = (
        har_summary.get("status") != "error" and final_page_evidence.get("status") == "complete"
    )
    status = (
        "pass"
        if execution_status == "pass" and process_passed and result_passed and evidence_passed
        else "fail"
    )
    trace.append(
        {
            "index": len(task.tool_calls) + 1,
            "action": "evaluate_result",
            "arguments": {"evaluator": (task.evaluator or {}).get("kind")},
            "status": "pass" if result_passed else "fail",
            "duration_ms": 0.0,
            "output": "evaluator passed" if result_passed else error,
            "checks": validation,
        }
    )
    return {
        "engine": engine,
        "task_id": task.task_id,
        "title": task.title,
        "complexity": task.complexity,
        "tags": list(task.tags),
        "step_count": task.step_count,
        "attempt": attempt,
        "is_retry": is_retry,
        "status": status,
        "result_status": "pass" if result_passed else "fail",
        "process_status": "pass" if process_passed else "fail",
        "combined_status": (
            "pass" if result_passed and process_passed and evidence_passed else "fail"
        ),
        "duration_ms": round(duration_ms, 3),
        "tool_output_tokens": sum(len(ENCODING.encode(value)) for value in outputs.values()),
        "output_chars": sum(len(value) for value in outputs.values()),
        "error": error,
        "trace": trace,
        "validation": validation,
        "evaluator": (task.evaluator or {}).get("kind"),
        "evaluator_status": evaluation.status if evaluation is not None else "error",
        "network_har": har_summary,
        "final_page_evidence": final_page_evidence,
        "evidence_status": "pass" if evidence_passed else "fail",
        "evidence_directory": str(artifact_dir),
        "process_validation": {
            "expected_actions": expected_actions,
            "actual_actions": actual_actions,
            "all_calls_passed": bool(tool_trace)
            and all(item.get("status") == "pass" for item in tool_trace),
            "passed": process_passed,
        },
        "resource": _resource_result(resources, sampler, before, after, duration_ms),
    }


def _environment_failure_row(
    engine: str,
    task: BrowserTask,
    attempt: int,
    artifact_dir: Path,
    *,
    is_retry: bool,
    duration_ms: float,
    error: str,
) -> dict[str, Any]:
    """Represent reset/prepare failure as a fail-closed physical attempt."""
    expected_actions = list(task.ground_truth.get("expected_actions") or [])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trace = [
        {
            "index": 0,
            "action": "prepare_environment",
            "arguments": {"environment": (task.environment or {}).get("kind")},
            "status": "fail",
            "duration_ms": round(duration_ms, 3),
            "output": "environment reset or preparation failed before trajectory execution",
        }
    ]
    return {
        "engine": engine,
        "task_id": task.task_id,
        "title": task.title,
        "complexity": task.complexity,
        "tags": list(task.tags),
        "step_count": task.step_count,
        "attempt": attempt,
        "is_retry": is_retry,
        "status": "fail",
        "result_status": "fail",
        "process_status": "fail",
        "combined_status": "fail",
        "duration_ms": round(duration_ms, 3),
        "tool_output_tokens": 0,
        "output_chars": 0,
        "error": error,
        "trace": trace,
        "validation": [],
        "evaluator": (task.evaluator or {}).get("kind"),
        "evaluator_status": "not_run",
        "network_har": {"status": "error", "reason": "environment_prepare_failed"},
        "final_page_evidence": {
            "status": "error",
            "reason": "environment_prepare_failed",
        },
        "evidence_status": "fail",
        "evidence_directory": str(artifact_dir),
        "process_validation": {
            "expected_actions": expected_actions,
            "actual_actions": [],
            "all_calls_passed": False,
            "passed": False,
        },
        "resource": {},
        "termination_reason": "environment_prepare_failed",
    }




class _QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def _start_fixture_server(root: Path) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    handler = partial(_QuietFixtureHandler, directory=str(root))
    # Linux containers reach the host through the Docker bridge, so binding
    # loopback would make this suite-scoped, ephemeral fixture server unreachable.
    server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="browser-eval-fixtures",
        daemon=True,
    )
    thread.start()
    origin = f"http://host.docker.internal:{server.server_port}"
    return server, thread, origin


def _materialize_task(task: BrowserTask, fixture_origin: str) -> BrowserTask:
    calls = []
    for call in task.tool_calls:
        item = dict(call)
        for key, value in tuple(item.items()):
            if isinstance(value, str):
                item[key] = value.replace("{fixture_origin}", fixture_origin)
        calls.append(item)
    return BrowserTask(
        task_id=task.task_id,
        title=task.title,
        tool_calls=tuple(calls),
        assertions=task.assertions,
        tags=task.tags,
        difficulty=task.difficulty,
        ground_truth=task.ground_truth,
        source=task.source,
        evaluator=task.evaluator,
        environment=task.environment,
        trajectory=task.trajectory,
    )


def _bounded_authoring_task(task: BrowserTask, timeout_ms: int = 5_000) -> BrowserTask:
    """Cap diagnostic target waits even for ad-hoc repaired authoring suites."""
    calls: list[dict[str, Any]] = []
    for raw in task.tool_calls:
        call = dict(raw)
        if call.get("selector") is not None or isinstance(call.get("target"), dict):
            call["timeout_ms"] = min(int(call.get("timeout_ms", timeout_ms)), timeout_ms)
        if isinstance(call.get("target"), dict):
            call["target_timeout_ms"] = min(
                int(call.get("target_timeout_ms", timeout_ms)), timeout_ms
            )
        calls.append(call)
    trajectory = dict(task.trajectory or {})
    trajectory["actions"] = calls
    return BrowserTask(
        task_id=task.task_id,
        title=task.title,
        tool_calls=tuple(calls),
        assertions=task.assertions,
        tags=task.tags,
        difficulty=task.difficulty,
        ground_truth=task.ground_truth,
        source=task.source,
        evaluator=task.evaluator,
        environment=task.environment,
        trajectory=trajectory,
    )


def _environment(client: Any, containers: dict[str, Any]) -> dict[str, Any]:
    info = client.info()
    version_commands = {"chromium": ["chromium", "--version"], "moli": ["moli", "--version"]}
    browser_versions: dict[str, str] = {}
    for engine, container in containers.items():
        try:
            result = container.exec_run(version_commands[engine])
            output = result.output.decode("utf-8", errors="replace").strip()
            browser_versions[engine] = output or f"exit {result.exit_code}, no output"
        except Exception as exc:
            browser_versions[engine] = f"unavailable: {type(exc).__name__}"
    return {
        "runner_platform": platform.platform(),
        "docker_server_version": client.version().get("Version"),
        "docker_os": info.get("OperatingSystem"),
        "docker_architecture": info.get("Architecture"),
        "docker_cpus": info.get("NCPU"),
        "docker_memory_bytes": info.get("MemTotal"),
        "cgroup_version": info.get("CgroupVersion"),
        "browser_versions": browser_versions,
        "images": {
            engine: {
                "id": container.image.id,
                "tags": container.image.tags,
                "build_source": {
                    key: value
                    for key, value in (
                        (container.image.attrs.get("Config") or {}).get("Labels") or {}
                    ).items()
                    if key.startswith("lex.browser-use-eval.")
                },
            }
            for engine, container in containers.items()
        },
    }






def _write_run_artifacts(
    output: Path, rows: list[dict[str, Any]], summary: dict[str, Any], manifest: dict[str, Any]
) -> None:
    attempts_bytes = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode(
        "utf-8"
    )
    summary_bytes = (json.dumps(summary, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    (output / "attempts.jsonl").write_bytes(attempts_bytes)
    (output / "summary.json").write_bytes(summary_bytes)
    manifest["artifact_provenance"] = {
        "artifact_directory": str(output),
        "attempts_sha256": hashlib.sha256(attempts_bytes).hexdigest(),
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def run_evaluation(
    *,
    suite_path: str | Path,
    output_dir: str | Path,
    reports_dir: str | Path,
    ground_truth_evidence_path: str | Path | None = None,
    max_retries: int = 1,
    sample_interval_ms: int = 50,
    settle_ms: int = 1500,
    task_timeout_ms: int = 300_000,
    cpus: float = 2.0,
    memory: str = "2g",
    llm_diagnostics: bool = False,
    diagnosis_model: str = "gpt-5.6-sol",
    diagnosis_reasoning_effort: str = "low",
    webarena_config_path: str | Path | None = None,
    engines: tuple[str, ...] = ("chromium", "moli"),
    qualification_run: bool = False,
    qualification_pass_index: int | None = None,
    qualification_required_passes: int | None = None,
    trajectory_authoring_run: bool = False,
    task_ids: tuple[str, ...] | None = None,
    task_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if task_timeout_ms < 1:
        raise ValueError("task_timeout_ms must be positive")
    if cpus <= 0:
        raise ValueError("cpus must be positive for measured runs")
    if (
        not engines
        or len(set(engines)) != len(engines)
        or any(engine not in ENGINE_IMAGES for engine in engines)
    ):
        raise ValueError(f"engines must be a unique subset of {tuple(ENGINE_IMAGES)}")
    if qualification_run and trajectory_authoring_run:
        raise ValueError("qualification_run and trajectory_authoring_run are mutually exclusive")
    if qualification_run:
        if qualification_required_passes != 2 or qualification_pass_index not in {1, 2}:
            raise ValueError(
                "qualification requires pass index 1 or 2 and exactly two independent passes"
            )
    elif qualification_pass_index is not None or qualification_required_passes is not None:
        raise ValueError("qualification pass identity is valid only for qualification runs")
    if (qualification_run or trajectory_authoring_run) and (
        engines != ("chromium",) or max_retries != 0
    ):
        raise ValueError(
            "qualification and trajectory authoring require one Chromium attempt and no retries"
        )
    suite = load_suite(suite_path)
    declared_ids = {task.task_id for task in suite.tasks}
    selected_ids = set(task_ids or declared_ids)
    unknown_ids = selected_ids - declared_ids
    if unknown_ids:
        raise ValueError(f"unknown task ids: {sorted(unknown_ids)}")
    if not selected_ids:
        raise ValueError("task_ids must select at least one task")
    selected_tasks = tuple(task for task in suite.tasks if task.task_id in selected_ids)
    uses_webarena = any(
        (task.environment or {}).get("kind") == "webarena_verified" for task in selected_tasks
    )
    if uses_webarena and webarena_config_path is None:
        raise ValueError("--webarena-config is required for WebArena tasks")
    webarena_states = {
        task.task_id: (task.ground_truth.get("qualification") or {}).get("status")
        for task in selected_tasks
        if (task.environment or {}).get("kind") == "webarena_verified"
    }
    allowed_states = (
        {"pending"} if qualification_run or trajectory_authoring_run else {"eligible", "skipped"}
    )
    invalid_states = {
        task_id: state for task_id, state in webarena_states.items() if state not in allowed_states
    }
    if invalid_states:
        mode = (
            "qualification"
            if qualification_run
            else "trajectory authoring"
            if trajectory_authoring_run
            else "formal measurement"
        )
        raise ValueError(
            f"WebArena {mode} has invalid qualification states: {invalid_states}; "
            f"allowed states are {sorted(allowed_states)}"
        )
    runnable_tasks = tuple(
        task
        for task in selected_tasks
        if qualification_run
        or (task.ground_truth.get("qualification") or {}).get("status") != "skipped"
    )
    if trajectory_authoring_run:
        state_changing = [
            task.task_id
            for task in runnable_tasks
            if "mutate" in task.tags
            or not ({"retrieve", "navigate"} & set(task.tags))
        ]
        if state_changing:
            raise ValueError(
                "trajectory_authoring_run permits non-mutating retrieve/navigate tasks only: "
                + ", ".join(state_changing)
            )
    ground_truth_evidence = (
        validate_ground_truth_evidence(suite_path, suite, ground_truth_evidence_path)
        if ground_truth_evidence_path is not None
        else None
    )
    # Validate the benchmark/runtime binding before touching Docker. This keeps
    # a wrong environment config from wasting a remote daemon round trip or
    # starting any measured resource.
    webarena_environment = (
        WebArenaEnvironment(webarena_config_path) if webarena_config_path is not None else None
    )
    client = docker.from_env()
    validate_resource_controls(client.info())
    require_linux_cgroup_v2(client)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    containers: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    browsers: dict[str, Browser] = {}
    pages: dict[str, Page] = {}
    visited_origins: dict[str, set[str]] = {}
    resources: dict[str, DockerStatsResources] = {}
    rows: list[dict[str, Any]] = []
    evaluator_registry = EvaluatorRegistry(webarena_config_path=webarena_config_path)
    site_client = client
    close_site_client = False
    if webarena_environment is not None and uses_webarena:
        site_client, close_site_client = webarena_environment.site_docker_client(client)
    webarena_runtime = (
        webarena_environment.verify_runtime(site_client, runnable_tasks)
        if webarena_environment is not None and uses_webarena
        else None
    )
    playwright = sync_playwright().start()
    uses_local_fixtures = any(
        "{fixture_origin}" in str(value)
        for task in selected_tasks
        for call in task.tool_calls
        for value in call.values()
    )
    fixture_runtime: tuple[ThreadingHTTPServer, threading.Thread, str] | None = None
    if uses_local_fixtures:
        fixture_runtime = _start_fixture_server(Path(suite_path).resolve().parent / "fixtures")
    fixture_origin = fixture_runtime[2] if fixture_runtime else ""
    authoring_initialized_sites: set[str] = set()
    initialized_sites_by_engine = _engine_site_state(engines)
    try:
        for engine in engines:
            network = client.networks.create(
                f"lex-browser-use-eval-{run_id}-{engine}",
                driver="bridge",
                labels={
                    "lex.browser-use-eval.run": run_id,
                    "lex.browser-use-eval.engine": engine,
                },
            )
            networks[engine] = network
            container = start_browser_container(
                client,
                image=ENGINE_IMAGES[engine], cdp_port=ENGINE_PORTS[engine],
                engine=engine,
                run_id=run_id,
                cpus=cpus,
                memory=memory,
                network=network.name,
            )
            containers[engine] = container
            container.reload()
            cdp_port = ENGINE_PORTS[engine]
            endpoint = published_endpoint(container, engine=engine, cdp_port=cdp_port)
            websocket_url = wait_cdp(endpoint, container=container)
            browsers[engine] = playwright.chromium.connect_over_cdp(websocket_url)
            resources[engine] = DockerStatsResources(container)
            page = browsers[engine].contexts[0].pages[0]
            page.goto("https://example.com/", wait_until="domcontentloaded", timeout=30_000)
            _reset_state(page, {"https://example.com"})
            pages[engine] = page
            visited_origins[engine] = set()

        for task_index, declared_task in enumerate(runnable_tasks):
            task = _materialize_task(declared_task, fixture_origin)
            if trajectory_authoring_run:
                task = _bounded_authoring_task(task)
            base_task = task
            engine_order = (
                engines if len(engines) == 1 or task_index % 2 == 0 else tuple(reversed(engines))
            )
            for engine in engine_order:
                initialized_sites = initialized_sites_by_engine[engine]
                for attempt in range(1, max_retries + 2):
                    task = base_task
                    artifact_dir = (
                        output / "tasks" / task.task_id / engine / f"attempt-{attempt}"
                    )
                    environment_reset = None
                    attempt_started = time.perf_counter()
                    attempt_deadline = attempt_started + task_timeout_ms / 1000
                    try:
                        pages[engine] = _prepare_page(
                            pages[engine],
                            visited_origins[engine],
                            settle_ms=settle_ms,
                            viewport=_task_viewport(task),
                        )
                        if (task.environment or {}).get("kind") == "webarena_verified":
                            assert webarena_environment is not None
                            task_sites = set((task.environment or {}).get("sites") or [])
                            if (
                                trajectory_authoring_run
                                and task_sites <= authoring_initialized_sites
                            ):
                                environment_reset = {
                                    "diagnostic_only": True,
                                    "mode": "warm_non_mutating_authoring_reuse",
                                    "sites": sorted(task_sites),
                                }
                            elif _may_reuse_webarena_site(task, initialized_sites):
                                environment_reset = {
                                    "diagnostic_only": bool(qualification_run),
                                    "mode": "warm_non_mutating_site_reuse",
                                    "sites": sorted(task_sites),
                                }
                            else:
                                if "mutate" in task.tags:
                                    initialized_sites.difference_update(task_sites)
                                reset_budget_ms = _remaining_task_budget_ms(
                                    attempt_deadline, phase="environment reset"
                                )
                                environment_reset = webarena_environment.reset(
                                    task,
                                    site_client,
                                    timeout_seconds=reset_budget_ms / 1000,
                                )
                                if trajectory_authoring_run:
                                    authoring_initialized_sites.update(task_sites)
                                if "mutate" not in task.tags:
                                    initialized_sites.update(task_sites)
                            task = webarena_environment.materialize_task_actions(base_task)
                            prepare_budget_ms = _remaining_task_budget_ms(
                                attempt_deadline, phase="environment prepare"
                            )
                            pages[engine] = webarena_environment.prepare(
                                task,
                                pages[engine],
                                timeout_seconds=prepare_budget_ms / 1000,
                            )
                        execution_budget_ms = _remaining_task_budget_ms(
                            attempt_deadline, phase="trajectory execution"
                        )
                    except Exception:
                        row = _environment_failure_row(
                            engine,
                            task,
                            attempt,
                            artifact_dir,
                            is_retry=attempt > 1,
                            duration_ms=(time.perf_counter() - attempt_started) * 1000,
                            error=traceback.format_exc(),
                        )
                    else:
                        row = _run_task(
                            engine,
                            pages[engine],
                            visited_origins[engine],
                            resources[engine],
                            task,
                            attempt,
                            sample_interval_ms,
                            evaluator_registry,
                            artifact_dir,
                            is_retry=attempt > 1,
                            task_timeout_ms=execution_budget_ms,
                        )
                        row["execution_duration_ms"] = row["duration_ms"]
                        row["duration_ms"] = round(
                            (time.perf_counter() - attempt_started) * 1000, 3
                        )
                    row["run_id"] = run_id
                    row["qualification_run"] = qualification_run
                    row["qualification_pass_index"] = (
                        qualification_pass_index if qualification_run else None
                    )
                    row["qualification_required_passes"] = (
                        qualification_required_passes if qualification_run else None
                    )
                    row["trajectory_authoring_run"] = trajectory_authoring_run
                    row["environment_reset"] = environment_reset
                    row["task_contract_sha256"] = task_contract_sha256(
                        {
                            "id": task.task_id,
                            "title": task.title,
                            "trajectory": task.trajectory,
                            "evaluator": task.evaluator,
                            "environment": task.environment,
                            "ground_truth": task.ground_truth,
                            "source": task.source,
                        }
                    )
                    attempt_path = artifact_dir / "attempt.json"
                    attempt_path.parent.mkdir(parents=True, exist_ok=True)
                    attempt_path.write_text(
                        json.dumps(row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                    )
                    rows.append(row)
                    print(
                        json.dumps(
                            {
                                "engine": row["engine"],
                                "task_id": row["task_id"],
                                "attempt": row["attempt"],
                                "status": row["status"],
                                "duration_ms": row["duration_ms"],
                                "error": next(
                                    (
                                        line
                                        for line in reversed(row["error"].splitlines())
                                        if line.strip()
                                    ),
                                    "",
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    if row["status"] == "pass":
                        break
        summary = summarize_attempts(rows, engines=engines)
        manifest = {
            "schema": "lex_browser_use_manifest/2",
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "suite_sha256": hashlib.sha256(Path(suite_path).read_bytes()).hexdigest(),
            "suite": [
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "step_count": task.step_count,
                    "complexity": task.complexity,
                    "tags": list(task.tags),
                    "tool_calls": list(task.tool_calls),
                    "assertions": list(task.assertions),
                    "ground_truth": task.ground_truth,
                    "source": task.source,
                    "trajectory": task.trajectory,
                    "evaluator": task.evaluator,
                    "environment": task.environment,
                }
                for task in runnable_tasks
            ],
            "ground_truth_validation": (
                {
                    key: ground_truth_evidence.get(key)
                    for key in (
                        "schema",
                        "dataset",
                        "dataset_sha256",
                        "source_commit",
                        "validator",
                        "method",
                        "started_at",
                        "finished_at",
                        "automatic_browser_replay",
                        "human_review",
                    )
                }
                if ground_truth_evidence is not None
                else None
            ),
            "protocol": {
                "browser_reuse": True,
                "viewport": {
                    "repository_curated": {"width": 1280, "height": 900},
                    "webarena_verified": {"width": 1280, "height": 720},
                },
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
                "browser_state_reset_between_tasks": True,
                "browser_state_reset_method": (
                    "warm browser/context; clear all top-level navigation origins, browser "
                    "cache, cookies, permissions and history; replace renderer page"
                ),
                "webarena_site_reset_policy": (
                    "reset once per site and engine for a contiguous/non-mutating task set; "
                    "reset before every state-changing task and invalidate reuse afterward"
                ),
                "sample_interval_ms": sample_interval_ms,
                "post_reset_settle_ms": settle_ms,
                "task_timeout_ms": task_timeout_ms,
                "primary_attempts_per_task": 1,
                "max_failure_retries": max_retries,
                "engine_order": "balanced by task index",
                "engines": list(engines),
                "qualification_run": qualification_run,
                "qualification_pass_index": (
                    qualification_pass_index if qualification_run else None
                ),
                "qualification_required_passes": (
                    qualification_required_passes if qualification_run else None
                ),
                "trajectory_authoring_run": trajectory_authoring_run,
                "trajectory_authoring_scope": (
                    "diagnostic-only non-mutating retrieve/navigate tasks; one site reset before "
                    "each site's warm batch; never accepted as qualification evidence"
                    if trajectory_authoring_run
                    else None
                ),
                "task_filter": [task.task_id for task in runnable_tasks],
                "task_selection": task_selection,
                "skipped_qualified_tasks": [
                    task.task_id
                    for task in selected_tasks
                    if (task.ground_truth.get("qualification") or {}).get("status") == "skipped"
                ],
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
                "required_attempt_evidence": [
                    "attempt.json",
                    "network.har",
                    "final.png",
                    "final-page.json",
                ],
                "missing_required_evidence": "attempt fails closed",
            },
            "environment": _environment(client, containers),
            "webarena_runtime": webarena_runtime,
        }
        smoke_path = Path(suite_path).resolve().parent / "agent_smoke.json"
        if smoke_path.exists():
            smoke_bytes = smoke_path.read_bytes()
            manifest["agent_smoke"] = {
                **json.loads(smoke_bytes.decode("utf-8")),
                "provenance": {
                    "source_file": smoke_path.name,
                    "source_sha256": hashlib.sha256(smoke_bytes).hexdigest(),
                    "independent_of_browser_microbenchmark": True,
                    "reused_not_rerun": True,
                },
            }
        # Persist core evidence before optional remote diagnostics can fail or stall.
        _write_run_artifacts(output, rows, summary, manifest)
        diagnose_summary(
            summary,
            use_llm=llm_diagnostics,
            model=diagnosis_model,
            reasoning_effort=diagnosis_reasoning_effort,
        )
        _write_run_artifacts(output, rows, summary, manifest)
        if not qualification_run and not trajectory_authoring_run:
            write_latest_report(reports_dir, manifest=manifest, summary=summary)
        return summary
    finally:
        if fixture_runtime:
            fixture_server, fixture_thread, _ = fixture_runtime
            fixture_server.shutdown()
            fixture_server.server_close()
            fixture_thread.join(timeout=2)
        cleanup_browser_runtime(browsers, playwright, containers, networks)
        if close_site_client:
            site_client.close()
