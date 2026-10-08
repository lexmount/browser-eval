"""Diagnostic-only exact frozen action replay against a remote read-only WebArena task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, required=True)
    browser = parser.add_mutually_exclusive_group(required=True)
    browser.add_argument("--binary", type=Path)
    browser.add_argument("--docker-image")
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--cowagent-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cowagent = args.cowagent_repo.resolve()
    browser_tool_source = cowagent / "agent/tools/browser/browser_tool.py"
    if not browser_tool_source.is_file():
        raise ValueError("CowAgent BrowserTool source is missing")
    sys.path.insert(0, str(cowagent))
    from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

    from harness.environments.webarena.runtime import (
        browser_tool_config,
        cdp_metadata,
        login_context,
        prepare_browser_viewport,
    )
    from harness.trajectory_replay.remote_runtime import (  # noqa: PLC0415
        load_remote_sites,
        rebind_remote_calls,
        remote_benchmark,
    )
    from harness.environments.browser import external_websocket_url  # noqa: PLC0415
    from harness.trajectory_replay.webarena_replay import _execute_replay_call  # noqa: PLC0415
    os.umask(0o077)
    output = args.output.resolve()
    if output.exists():
        raise ValueError("output exists; no implicit retry or overwrite")
    binary = args.binary.resolve() if args.binary else None
    docker_client = None
    docker_image = None
    if args.docker_image:
        import docker  # noqa: PLC0415

        docker_client = docker.from_env()
        docker_image = docker_client.images.get(args.docker_image)
        if not docker_image.id.startswith("sha256:"):
            raise ValueError("Docker browser image identity is unavailable")
    site_map = args.site_map.resolve()
    dataset_path = ROOT / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
    dataset = json.loads(dataset_path.read_text())
    rows = [row for row in dataset["trajectories"] if row["task_id"] == args.task]
    if len(rows) != 1:
        raise ValueError("frozen trajectory task is missing or duplicated")
    frozen_calls = rows[0]["tool_calls"]
    sites = load_remote_sites(site_map)
    container_hosts = {
        host: socket.gethostbyname(host)
        for host in {
            urllib.parse.urlsplit(url).hostname for url in sites.values()
        }
        if host
    } if docker_image else {}
    benchmark = remote_benchmark(sites)
    official = benchmark.get_task(args.task)
    if not official.is_retrieve_task:
        raise ValueError("this no-reset diagnostic supports read-only retrieval tasks only")
    rebound = rebind_remote_calls(frozen_calls, sites, {"192.168.5.2", "host.docker.internal"})
    if any(
        marker in str(call.get("selector", ""))
        for call in rebound for marker in ("192.168.5.2", "host.docker.internal")
    ):
        raise ValueError("absolute selector host requires a separately qualified rebind")
    output.mkdir(mode=0o700, parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    endpoint = f"http://127.0.0.1:{port}"
    receipt = {
        "schema": "remote_exact_frozen_actions_diagnostic/1",
        "task_id": args.task,
        "status": "started",
        "promotable": False,
        "official_evaluation": "not_run",
        "site_reset": False,
        "container_hosts": container_hosts,
        "frozen_action_count": len(frozen_calls),
        "frozen_calls_sha256": digest(frozen_calls),
        "rebound_calls_sha256": digest(rebound),
        "input_sha256": {
            "binary": sha256(binary) if binary else None,
            "docker_image": docker_image.id if docker_image else None,
            "site_map": sha256(site_map),
            "dataset": sha256(dataset_path), "runner": sha256(Path(__file__)),
            "cowagent_browser_tool": sha256(browser_tool_source),
        },
        "steps": [],
    }
    save(output / "receipt.json", receipt)
    process = None
    container = None
    tool = None
    try:
        with tempfile.TemporaryDirectory(prefix="moli-exact-actions-") as profile:
            if binary:
                with (output / "browser.log").open("x") as log:
                    process = subprocess.Popen(
                        [str(binary), "serve", "--host", "127.0.0.1", "--port", str(port),
                         "--layout", "--font", "--profile-dir", profile],
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                    )
            else:
                container = docker_client.containers.run(
                    docker_image.id,
                    detach=True,
                    ports={"9222/tcp": ("127.0.0.1", port)},
                    mem_limit="4g",
                    pids_limit=512,
                    shm_size="512m",
                    security_opt=["no-new-privileges"],
                    extra_hosts=container_hosts,
                    labels={"lex.browser-use-eval.scope": "remote-exact-read-only-diagnostic"},
                )
                receipt["container_id"] = container.id
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process is not None and process.poll() is not None:
                    raise RuntimeError(f"Moli exited {process.returncode}")
                if container is not None:
                    container.reload()
                    if container.status != "running":
                        raise RuntimeError(f"Moli container exited: {container.status}")
                try:
                    with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as reply:
                        if reply.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("Moli CDP not ready")
            cdp_endpoint = endpoint
            if container is not None:
                with urllib.request.urlopen(endpoint + "/json/version", timeout=2) as reply:
                    advertised = str(json.load(reply).get("webSocketDebuggerUrl") or "")
                if not advertised:
                    raise RuntimeError("Docker CDP response omitted webSocketDebuggerUrl")
                cdp_endpoint = external_websocket_url(endpoint, advertised)
            tool = BrowserTool(config=browser_tool_config(cdp_endpoint))
            service = tool._get_service()
            receipt["browser"] = cdp_metadata(endpoint)
            receipt["viewport"] = prepare_browser_viewport(service)
            official_sites = tuple(official.sites)
            receipt["login_retries"] = login_context(
                service, official_sites, benchmark,
                [sites[site] for site in official_sites], receipt["viewport"],
            )
            for index, call in enumerate(rebound, 1):
                started = time.perf_counter()
                status, result = _execute_replay_call(tool, call)
                row = {
                    "index": index, "arguments": call,
                    "tool_status": status, "result": result,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                }
                receipt["steps"].append(row)
                save(output / "trace.json", receipt["steps"])
                if status != "success":
                    try:
                        receipt["failed_action_observation"] = service._submit(
                            lambda: service._page.evaluate("""() => {
                              const matches=[...document.querySelectorAll('button,a')]
                                .filter(e=>e.textContent.trim()==='Filters' ||
                                  e.textContent.includes('Clear all') ||
                                  e.getAttribute('href')?.includes('/admin/sales/order/view/order_id/230/'));
                              return {url:location.href,readyState:document.readyState,
                                bodyTextLength:document.body?.innerText?.length??null,
                                orderGridPresent:!!document.querySelector('[data-role="grid-wrapper"],.admin__data-grid-wrap'),
                                matches:matches.map(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
                                  const p=e.parentElement?.getBoundingClientRect();
                                  return {tag:e.tagName,text:e.textContent.trim().slice(0,120),
                                    href:e.getAttribute('href'),rect:[r.x,r.y,r.width,r.height],
                                    parentRect:p?[p.x,p.y,p.width,p.height]:null,
                                    offsetParentTag:e.offsetParent?.tagName??null,
                                    display:s.display,visibility:s.visibility,disabled:e.disabled??null};})};
                            }""")
                        )
                        service._submit(
                            lambda: service._page.screenshot(path=str(output / "failure-observation.png"))
                        )
                    except Exception as observation_error:
                        receipt["failed_action_observation_error"] = (
                            f"{type(observation_error).__name__}: {observation_error}"
                        )
                    raise RuntimeError(f"frozen action {index} failed: {result}")
            receipt["status"] = "exact_actions_complete"
            receipt["completed_action_count"] = len(rebound)
            receipt["final_url"] = service._submit(lambda: service._page.url)
            receipt["final_observation"] = service._submit(
                lambda: service._page.locator("body").inner_text()[:500]
            )
            return 0
    except Exception:
        receipt["status"] = "diagnostic_incomplete"
        receipt["error"] = traceback.format_exc()
        receipt["completed_action_count"] = sum(
            row["tool_status"] == "success" for row in receipt["steps"]
        )
        return 1
    finally:
        if tool is not None:
            try:
                tool._get_service().close()
            except Exception:
                pass
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if container is not None:
            try:
                (output / "browser.log").write_bytes(container.logs())
                container.remove(force=True)
            except Exception as exc:
                receipt["container_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        if (output / "trace.json").is_file():
            receipt["trace_sha256"] = sha256(output / "trace.json")
        if (output / "failure-observation.png").is_file():
            receipt["failure_observation_sha256"] = sha256(output / "failure-observation.png")
        save(output / "receipt.json", receipt)
        print(json.dumps({"task_id": args.task, "status": receipt["status"],
                          "completed": receipt.get("completed_action_count"),
                          "actions": len(frozen_calls), "output": str(output)}))


if __name__ == "__main__":
    raise SystemExit(main())
