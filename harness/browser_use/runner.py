"""Docker-isolated browser use evaluation runner."""

from __future__ import annotations

import hashlib
import json
import platform
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import docker
import tiktoken
from playwright.sync_api import Browser, Page, sync_playwright

from .executor import execute_steps, validate_outputs
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
) -> dict[str, int | float]:
    return {**delta_counters(before, after), **sampler.summary()}


def _published_endpoint(container: Any, *, engine: str, cdp_port: int) -> str:
    ports = ((container.attrs.get("NetworkSettings") or {}).get("Ports") or {})
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
    error = ""
    try:
        outputs = execute_steps(page, task.steps)
        failures = validate_outputs(outputs, task.assertions)
        status = "pass" if not failures else "fail"
        error = "; ".join(failures)
    except Exception as exc:
        status = "fail"
        error = f"{type(exc).__name__}: {exc}"
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
        "step_count": task.step_count,
        "attempt": attempt,
        "status": status,
        "duration_ms": round(duration_ms, 3),
        "tool_output_tokens": sum(len(ENCODING.encode(value)) for value in outputs.values()),
        "output_chars": sum(len(value) for value in outputs.values()),
        "error": error[:1000],
        "resource": _resource_result(resources, sampler, before, after),
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


def run_evaluation(
    *,
    suite_path: str | Path,
    output_dir: str | Path,
    reports_dir: str | Path,
    repeats: int = 5,
    sample_interval_ms: int = 50,
    settle_ms: int = 1500,
    cpus: float = 2.0,
    memory: str = "2g",
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
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

        for attempt in range(1, repeats + 1):
            engine_order = ("chromium", "moli") if attempt % 2 else ("moli", "chromium")
            for task in suite.tasks:
                for engine in engine_order:
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
                    )
                    rows.append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
        summary = summarize_attempts(rows, engines=tuple(ENGINE_IMAGES))
        manifest = {
            "schema": "lex_browser_use_manifest/1",
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "suite_sha256": hashlib.sha256(Path(suite_path).read_bytes()).hexdigest(),
            "suite": [
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "step_count": task.step_count,
                    "complexity": task.complexity,
                }
                for task in suite.tasks
            ],
            "protocol": {
                "browser_reuse": True,
                "cold_start_excluded": True,
                "state_reset_between_tasks": True,
                "state_reset_method": (
                    "warm browser/context; clear all top-level navigation origins, "
                    "browser cache, cookies, permissions and history; replace renderer page"
                ),
                "sample_interval_ms": sample_interval_ms,
                "post_reset_settle_ms": settle_ms,
                "repeats": repeats,
                "engine_order": "balanced alternating",
                "resource_scope": "browser container cgroup/network namespace",
                "controller_excluded": True,
                "cpus_per_browser": cpus,
                "memory_per_browser": memory,
                "network_isolation": "one dedicated bridge with no peer containers per engine",
            },
            "environment": _environment(client, containers),
        }
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
        for browser in browsers.values():
            try:
                browser.close()
            except Exception:
                pass
        playwright.stop()
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
