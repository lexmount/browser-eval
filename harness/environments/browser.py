"""Container launch and CDP readiness shared by browser evaluation tracks."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BROWSER_IMAGES = {
    "chromium": "lex-browser-use-eval-chromium:local",
    "moli": "lex-browser-use-eval-moli:local",
}
ENGINE_PORTS = {"chromium": 9223, "moli": 9222}


def configured_browser_images() -> dict[str, str]:
    images = dict(DEFAULT_BROWSER_IMAGES)
    images["moli"] = os.environ.get("BROWSER_EVAL_MOLI_IMAGE", images["moli"])
    return images


def external_websocket_url(endpoint: str, advertised: str) -> str:
    endpoint_parts = urllib.parse.urlsplit(endpoint)
    websocket_parts = urllib.parse.urlsplit(advertised)
    scheme = "wss" if endpoint_parts.scheme == "https" else "ws"
    return urllib.parse.urlunsplit(
        (scheme, endpoint_parts.netloc, websocket_parts.path, websocket_parts.query, "")
    )


def wait_cdp(url: str, timeout_seconds: float = 60, *, container: Any = None) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/json/version", timeout=2) as response:
                if response.status == 200:
                    payload = json.load(response)
                    advertised = str(payload.get("webSocketDebuggerUrl") or "")
                    if advertised:
                        return external_websocket_url(url, advertised)
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


def published_endpoint(container: Any, *, engine: str, cdp_port: int) -> str:
    ports = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
    bindings = ports.get(f"{cdp_port}/tcp")
    if not bindings or not bindings[0].get("HostPort"):
        raise RuntimeError(f"{engine}: Docker did not publish CDP port")
    return f"http://127.0.0.1:{bindings[0]['HostPort']}"


def start_browser_container(
    client: Any,
    *,
    engine: str,
    image: str,
    cdp_port: int,
    run_id: str,
    cpus: float | None,
    memory: str,
    network: str,
) -> Any:
    name = f"lex-browser-use-eval-{run_id}-{engine}"
    options: dict[str, Any] = {
        "name": name,
        "detach": True,
        "ports": {f"{cdp_port}/tcp": ("127.0.0.1", None)},
        "labels": {
            "lex.browser-use-eval.run": run_id,
            "lex.browser-use-eval.engine": engine,
        },
        "mem_limit": memory,
        "pids_limit": 512,
        "shm_size": "512m",
        "security_opt": ["no-new-privileges"],
        "network": network,
        "extra_hosts": {"host.docker.internal": "host-gateway"},
    }
    if cpus is not None:
        if cpus <= 0:
            raise ValueError("cpus must be positive when a CPU limit is requested")
        options["nano_cpus"] = int(cpus * 1_000_000_000)
    return client.containers.run(image, **options)


def validate_resource_controls(info: dict[str, Any]) -> None:
    required = {
        "CpuCfsQuota": "CPU CFS quota",
        "CpuCfsPeriod": "CPU CFS period",
        "MemoryLimit": "memory limit",
        "PidsLimit": "PID limit",
    }
    unsupported = [label for key, label in required.items() if info.get(key) is False]
    if unsupported:
        raise RuntimeError(
            "Docker daemon cannot enforce measured resource controls: "
            + ", ".join(unsupported)
            + ". Fix cgroup delegation before qualification or formal measurement."
        )


def cleanup_browser_runtime(
    browsers: dict[str, Any],
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
