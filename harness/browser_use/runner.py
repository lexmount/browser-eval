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

from .executor import execute_steps, judge_outputs, validate_outputs
from .metrics import (
    DockerStatsResources,
    ResourceSampler,
    delta_counters,
    summarize_attempts,
)
from .report import write_latest_report
from .suite import BrowserTask, load_suite

ENGINE_IMAGES = {
    "chromium": "lex-browser-use-eval-chromium:local",
    "moli": "lex-browser-use-eval-moli:local",
}
ENGINE_PORTS = {"chromium": 9223, "moli": 9222}
ENCODING = tiktoken.get_encoding("o200k_base")


def _external_websocket_url(endpoint: str, advertised: str) -> str:
    endpoint_parts = urllib.parse.urlsplit(endpoint)
    websocket_parts = urllib.parse.urlsplit(advertised)
    scheme = "wss" if endpoint_parts.scheme == "https" else "ws"
    return urllib.parse.urlunsplit(
        (scheme, endpoint_parts.netloc, websocket_parts.path, websocket_parts.query, "")
    )


def _wait_cdp(url: str, timeout_seconds: float = 60, *, container: Any = None) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/json/version", timeout=2) as response:
                if response.status == 200:
                    payload = json.load(response)
                    advertised = str(payload.get("webSocketDebuggerUrl") or "")
                    if advertised:
                        return _external_websocket_url(url, advertised)
                    raise RuntimeError("CDP version response omitted webSocketDebuggerUrl")
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    logs = ""
    if container is not None:
        try:
            container.reload()
            logs = container.logs(tail=30).decode("utf-8", errors="replace")
        except Exception:
            pass
    detail = f"; container logs:\n{logs}" if logs else ""
    raise RuntimeError(f"CDP endpoint did not become ready: {last_error}{detail}")


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


def _published_endpoint(container: Any, *, engine: str, cdp_port: int) -> str:
    ports = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
    bindings = ports.get(f"{cdp_port}/tcp")
    if not bindings or not bindings[0].get("HostPort"):
        raise RuntimeError(f"{engine}: Docker did not publish CDP port")
    return f"http://127.0.0.1:{bindings[0]['HostPort']}"


def _prepare_page(page: Page, visited_origins: set[str], *, settle_ms: int) -> Page:
    """Reset task state and replace only the renderer page, keeping the browser/context warm."""
    context = page.context
    _reset_state(page, visited_origins)
    visited_origins.clear()
    page.close(run_before_unload=False)
    replacement = context.new_page()
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
        outputs = execute_steps(page, task.tool_calls, trace=trace)
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
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        sampler.stop()
        page.remove_listener("framenavigated", record_origin)
    after = resources.counters()
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
        "duration_ms": round(duration_ms, 3),
        "tool_output_tokens": sum(len(ENCODING.encode(value)) for value in outputs.values()),
        "output_chars": sum(len(value) for value in outputs.values()),
        "error": error,
        "trace": trace,
        "validation": validation,
        "resource": _resource_result(resources, sampler, before, after, duration_ms),
    }


def _start_browser_container(
    client: Any,
    *,
    engine: str,
    run_id: str,
    cpus: float,
    memory: str,
    network: str,
) -> Any:
    name = f"lex-browser-use-eval-{run_id}-{engine}"
    cdp_port = ENGINE_PORTS[engine]
    return client.containers.run(
        ENGINE_IMAGES[engine],
        name=name,
        detach=True,
        ports={f"{cdp_port}/tcp": ("127.0.0.1", None)},
        labels={"lex.browser-use-eval.run": run_id, "lex.browser-use-eval.engine": engine},
        nano_cpus=int(cpus * 1_000_000_000),
        mem_limit=memory,
        pids_limit=512,
        shm_size="512m",
        security_opt=["no-new-privileges"],
        network=network,
        extra_hosts={"host.docker.internal": "host-gateway"},
    )


class _QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def _start_fixture_server(root: Path) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    handler = partial(_QuietFixtureHandler, directory=str(root))
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


def run_evaluation(
    *,
    suite_path: str | Path,
    output_dir: str | Path,
    reports_dir: str | Path,
    max_retries: int = 1,
    sample_interval_ms: int = 50,
    settle_ms: int = 1500,
    cpus: float = 2.0,
    memory: str = "2g",
) -> dict[str, Any]:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    suite = load_suite(suite_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
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
    fixture_server, fixture_thread, fixture_origin = _start_fixture_server(
        Path(suite_path).resolve().parent / "fixtures"
    )
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
            container = _start_browser_container(
                client,
                engine=engine,
                run_id=run_id,
                cpus=cpus,
                memory=memory,
                network=network.name,
            )
            containers[engine] = container
            container.reload()
            cdp_port = ENGINE_PORTS[engine]
            endpoint = _published_endpoint(container, engine=engine, cdp_port=cdp_port)
            websocket_url = _wait_cdp(endpoint, container=container)
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
            "protocol": {
                "browser_reuse": True,
                "step_definition": (
                    "one declared CowAgent BrowserTool-equivalent call; implicit waits excluded"
                ),
                "llm_scope": (
                    "none; deterministic BrowserTool-equivalent microbenchmark, not CowAgent E2E"
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
                "fixture_origin": "temporary self-hosted deterministic fixture server",
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
            manifest["agent_smoke"] = json.loads(smoke_path.read_text(encoding="utf-8"))
        (output / "attempts.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        (output / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_latest_report(reports_dir, manifest=manifest, summary=summary)
        return summary
    finally:
        fixture_server.shutdown()
        fixture_server.server_close()
        fixture_thread.join(timeout=2)
        _cleanup_runtime(browsers, playwright, containers, networks)
