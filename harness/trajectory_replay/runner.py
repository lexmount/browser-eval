"""Docker-isolated browser trajectory replay runner."""

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

from harness.environments.browser import (
    ENGINE_PORTS,
    configured_browser_images,
    published_endpoint,
    start_browser_container,
    wait_cdp,
)
from harness.resource_accounting import DockerStatsResources, ResourceSampler, delta_counters

from .diagnostics import diagnose_summary
from .executor import execute_steps, judge_outputs, validate_outputs
from .metrics import summarize_attempts
from .report import write_latest_report
from .suite import BrowserTask, load_suite, validate_ground_truth_evidence

ENGINE_IMAGES = configured_browser_images()
ENCODING = tiktoken.get_encoding("o200k_base")






def _http_origin(url: str) -> str | None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _reset_state(page: Page, visited_origins: set[str]) -> None:
    context = page.context
    origins = set(visited_origins)
    current_origin = _http_origin(page.url)
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




def _prepare_page(page: Page, visited_origins: set[str], *, settle_ms: int) -> Page:
    """Reset task state and replace only the renderer page, keeping the browser/context warm."""
    context = page.context
    _reset_state(page, visited_origins)
    visited_origins.clear()
    page.close(run_before_unload=False)
    replacement = context.new_page()
    replacement.set_viewport_size({"width": 1280, "height": 900})
    viewport = replacement.evaluate("() => ({width: innerWidth, height: innerHeight})")
    if viewport != {"width": 1280, "height": 900}:
        raise RuntimeError(f"browser viewport mismatch: {viewport}")
    replacement.wait_for_timeout(max(0, settle_ms))
    return replacement


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
    expected_actions = list(task.ground_truth.get("expected_actions") or [])
    tool_trace = [item for item in trace if item.get("action") != "validate_assertions"]
    actual_actions = [str(item.get("action") or "") for item in tool_trace]
    process_passed = bool(tool_trace) and actual_actions == expected_actions and all(
        item.get("status") == "pass" for item in tool_trace
    )
    result_passed = bool(validation) and all(item.get("passed") is True for item in validation)
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
        "combined_status": "pass" if result_passed and process_passed else "fail",
        "duration_ms": round(duration_ms, 3),
        "tool_output_tokens": sum(len(ENCODING.encode(value)) for value in outputs.values()),
        "output_chars": sum(len(value) for value in outputs.values()),
        "error": error,
        "trace": trace,
        "validation": validation,
        "process_validation": {
            "expected_actions": expected_actions,
            "actual_actions": actual_actions,
            "all_calls_passed": bool(tool_trace) and all(
                item.get("status") == "pass" for item in tool_trace
            ),
            "passed": process_passed,
        },
        "resource": _resource_result(resources, sampler, before, after, duration_ms),
    }




class _QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def _start_fixture_server(root: Path) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    handler = partial(_QuietFixtureHandler, directory=str(root))
    # Containers reach host.docker.internal through the bridge, not host loopback.
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
            }
            for engine, container in containers.items()
        },
    }


def _cleanup_runtime(
    browsers: dict[str, Browser],
    playwright: Any,
    containers: dict[str, Any],
    networks: dict[str, Any],
) -> None:
    for browser in browsers.values():
        try:
            browser.close()
        except Exception:
            pass
    try:
        playwright.stop()
    except Exception:
        pass
    for container in containers.values():
        try:
            container.remove(force=True)
        except Exception:
            pass
    for network in networks.values():
        try:
            network.remove()
        except Exception:
            pass


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
    cpus: float = 2.0,
    memory: str = "2g",
    llm_diagnostics: bool = False,
    diagnosis_model: str = "gpt-5.6-sol",
    diagnosis_reasoning_effort: str = "low",
) -> dict[str, Any]:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    output = Path(output_dir).absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Output must not contain symlinks")
    if output.exists():
        raise ValueError("Output must be a new directory; existing evidence is immutable")
    suite = load_suite(suite_path)
    ground_truth_evidence = (
        validate_ground_truth_evidence(suite_path, suite, ground_truth_evidence_path)
        if ground_truth_evidence_path is not None
        else None
    )
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Output must not contain symlinks")
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ValueError(
            "Output must be a new directory; existing evidence is immutable"
        ) from error
    run_id = uuid.uuid4().hex[:12]
    client = docker.from_env()
    containers: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    browsers: dict[str, Browser] = {}
    pages: dict[str, Page] = {}
    visited_origins: dict[str, set[str]] = {}
    resources: dict[str, DockerStatsResources] = {}
    rows: list[dict[str, Any]] = []
    playwright = sync_playwright().start()
    uses_local_fixtures = any(
        "{fixture_origin}" in str(value)
        for task in suite.tasks
        for call in task.tool_calls
        for value in call.values()
    )
    fixture_runtime: tuple[ThreadingHTTPServer, threading.Thread, str] | None = None
    if uses_local_fixtures:
        fixture_runtime = _start_fixture_server(Path(suite_path).resolve().parent / "fixtures")
    fixture_origin = fixture_runtime[2] if fixture_runtime else ""
    try:
        for engine in ENGINE_IMAGES:
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

        for task_index, declared_task in enumerate(suite.tasks):
            task = _materialize_task(declared_task, fixture_origin)
            engine_order = ("chromium", "moli") if task_index % 2 == 0 else ("moli", "chromium")
            for engine in engine_order:
                for attempt in range(1, max_retries + 2):
                    pages[engine] = _prepare_page(
                        pages[engine],
                        visited_origins[engine],
                        settle_ms=settle_ms,
                    )
                    row = _run_task(
                        engine,
                        pages[engine],
                        visited_origins[engine],
                        resources[engine],
                        task,
                        attempt,
                        sample_interval_ms,
                        is_retry=attempt > 1,
                    )
                    row["run_id"] = run_id
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
        summary = summarize_attempts(rows, engines=tuple(ENGINE_IMAGES))
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
                }
                for task in suite.tasks
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
            "environment": _environment(client, containers),
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
        write_latest_report(reports_dir, manifest=manifest, summary=summary)
        return summary
    finally:
        if fixture_runtime:
            fixture_server, fixture_thread, _ = fixture_runtime
            fixture_server.shutdown()
            fixture_server.server_close()
            fixture_thread.join(timeout=2)
        _cleanup_runtime(browsers, playwright, containers, networks)
