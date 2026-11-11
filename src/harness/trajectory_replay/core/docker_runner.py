"""Docker runner code shared by both Trajectory Replay tracks.

Each track keeps its own ``run_evaluation`` and ``_run_task`` on top of these
helpers. Its task loop, evaluator, manifest protocol, report target and denominator
stay separate.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import platform
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import tiktoken
from playwright.sync_api import Browser, Page

from harness.environments.browser import (
    ENGINE_PORTS,
    published_endpoint,
    start_browser_container,
    wait_cdp,
)
from harness.environments.webarena.topology import container_host_origin
from harness.resource_accounting import DockerStatsResources, ResourceSampler, delta_counters
from harness.trajectory_replay.core.suite import BrowserTask


@cache
def _encoding() -> tiktoken.Encoding:
    # Loaded on first use: get_encoding may download the BPE file, which must
    # not happen merely by importing the runner (for example for CLI --help).
    return tiktoken.get_encoding("o200k_base")


def output_volume(outputs: dict[str, str]) -> dict[str, int]:
    return {
        "tool_output_tokens": sum(len(_encoding().encode(value)) for value in outputs.values()),
        "output_chars": sum(len(value) for value in outputs.values()),
    }


def http_origin(url: str) -> str | None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def main_frame_origin_recorder(page: Page, visited_origins: set[str]) -> Callable[[Any], None]:
    """Return a ``framenavigated`` listener that records top-level navigation origins."""

    def record_origin(frame: Any) -> None:
        if frame != page.main_frame:
            return
        origin = http_origin(str(frame.url))
        if origin:
            visited_origins.add(origin)

    return record_origin


def reset_browser_state(page: Page, origins: set[str]) -> None:
    """Clear browser state for ``origins`` and leave ``page`` on about:blank."""
    context = page.context
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


def open_task_page(context: Any, viewport: dict[str, int], *, settle_ms: int) -> Page:
    """Open the renderer page for one task and verify its viewport."""
    page = context.new_page()
    page.set_viewport_size(viewport)
    actual = page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    if actual != viewport:
        raise RuntimeError(f"browser viewport mismatch: {actual}")
    page.wait_for_timeout(max(0, settle_ms))
    return page


def resource_result(
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


def attempt_identity(
    engine: str, task: BrowserTask, attempt: int, *, is_retry: bool
) -> dict[str, Any]:
    """Return the fields that open every physical attempt row."""
    return {
        "engine": engine,
        "task_id": task.task_id,
        "title": task.title,
        "complexity": task.complexity,
        "tags": list(task.tags),
        "step_count": task.step_count,
        "attempt": attempt,
        "is_retry": is_retry,
    }


def process_validation(task: BrowserTask, trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the executed calls with the task's expected action sequence."""
    expected_actions = list(task.ground_truth.get("expected_actions") or [])
    tool_trace = [item for item in trace if item.get("action") != "validate_assertions"]
    actual_actions = [str(item.get("action") or "") for item in tool_trace]
    all_calls_passed = bool(tool_trace) and all(
        item.get("status") == "pass" for item in tool_trace
    )
    return {
        "expected_actions": expected_actions,
        "actual_actions": actual_actions,
        "all_calls_passed": all_calls_passed,
        "passed": all_calls_passed and actual_actions == expected_actions,
    }


def print_attempt_progress(row: dict[str, Any]) -> None:
    print(
        json.dumps(
            {
                "engine": row["engine"],
                "task_id": row["task_id"],
                "attempt": row["attempt"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "error": next(
                    (line for line in reversed(row["error"].splitlines()) if line.strip()),
                    "",
                ),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


class _QuietFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def start_fixture_server(root: Path) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    handler = partial(_QuietFixtureHandler, directory=str(root))
    # Linux containers reach the host through the Docker bridge, so binding
    # loopback would make this suite-scoped, ephemeral fixture server unreachable.
    server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="webstone-fixtures",
        daemon=True,
    )
    thread.start()
    origin = container_host_origin(server.server_port)
    return server, thread, origin


def stop_fixture_server(runtime: tuple[ThreadingHTTPServer, threading.Thread, str]) -> None:
    server, thread, _ = runtime
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def materialize_task(task: BrowserTask, fixture_origin: str) -> BrowserTask:
    calls = []
    for call in task.tool_calls:
        item = dict(call)
        for key, value in tuple(item.items()):
            if isinstance(value, str):
                item[key] = value.replace("{fixture_origin}", fixture_origin)
        calls.append(item)
    return dataclasses.replace(task, tool_calls=tuple(calls))


@dataclass
class EngineRuntime:
    """Started resources per engine, recorded as they start so cleanup covers a partial start."""

    networks: dict[str, Any] = field(default_factory=dict)
    containers: dict[str, Any] = field(default_factory=dict)
    browsers: dict[str, Browser] = field(default_factory=dict)
    resources: dict[str, DockerStatsResources] = field(default_factory=dict)
    pages: dict[str, Page] = field(default_factory=dict)
    visited_origins: dict[str, set[str]] = field(default_factory=dict)


def start_engine(
    runtime: EngineRuntime,
    client: Any,
    playwright: Any,
    *,
    engine: str,
    image: str,
    run_id: str,
    cpus: float,
    memory: str,
    reset_state: Callable[[Page, set[str]], None],
) -> None:
    """Start one engine on its own bridge and leave a warm, reset page in ``runtime``."""
    network = client.networks.create(
        f"lex-webstone-{run_id}-{engine}",
        driver="bridge",
        labels={
            "lex.webstone.run": run_id,
            "lex.webstone.engine": engine,
        },
    )
    runtime.networks[engine] = network
    cdp_port = ENGINE_PORTS[engine]
    container = start_browser_container(
        client,
        image=image,
        cdp_port=cdp_port,
        engine=engine,
        run_id=run_id,
        cpus=cpus,
        memory=memory,
        network=network.name,
    )
    runtime.containers[engine] = container
    container.reload()
    endpoint = published_endpoint(container, engine=engine, cdp_port=cdp_port)
    websocket_url = wait_cdp(endpoint, container=container)
    runtime.browsers[engine] = playwright.chromium.connect_over_cdp(websocket_url)
    runtime.resources[engine] = DockerStatsResources(container)
    page = runtime.browsers[engine].contexts[0].pages[0]
    page.goto("https://example.com/", wait_until="domcontentloaded", timeout=30_000)
    reset_state(page, {"https://example.com"})
    runtime.pages[engine] = page
    runtime.visited_origins[engine] = set()


def docker_environment(client: Any, containers: dict[str, Any]) -> dict[str, Any]:
    info = client.info()
    browser_versions: dict[str, str] = {}
    for engine, container in containers.items():
        try:
            result = container.exec_run([engine, "--version"])
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


def manifest_task(task: BrowserTask) -> dict[str, Any]:
    return {
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


def ground_truth_validation(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        key: evidence.get(key)
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


def write_run_artifacts(
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
