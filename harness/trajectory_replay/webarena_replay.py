"""Model-free replay of fixed Browser Use trajectories on WebArena-Verified."""

from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import docker

from harness.browser_use.har import CdpHarRecorder
from harness.browser_use.runner import (
    SITE_PORTS,
    _allow_official_container_hostname,
    _configure_benchmark,
    _container_host_gateway,
    _login_context,
    _physical_execution_lock,
    _physical_execution_lock_path,
    _prepare_sites,
    _resource_summary,
)
from harness.trajectory_replay.metrics import DockerStatsResources, ResourceSampler
from harness.trajectory_replay.runner import (
    ENGINE_IMAGES,
    ENGINE_PORTS,
    _published_endpoint,
    _start_browser_container,
    _wait_cdp,
)


def _candidate(task: dict[str, Any]) -> dict[str, Any]:
    candidate_id = task.get("canonical_candidate_id")
    for item in task.get("candidates") or []:
        if item.get("id") == candidate_id:
            return item
    raise ValueError(f"task {task.get('task_id')} has no canonical replay candidate")


def select_tasks(seed_manifest: dict[str, Any], task_ids: list[int]) -> list[dict[str, Any]]:
    by_id = {int(task["task_id"]): task for task in seed_manifest.get("tasks") or []}
    missing = sorted(set(task_ids) - set(by_id))
    if missing:
        raise ValueError(f"tasks are not present in the replay seed manifest: {missing}")
    selected = [by_id[task_id] for task_id in task_ids]
    blocked = [int(task["task_id"]) for task in selected if task.get("replay_status") != "ready"]
    if blocked:
        raise ValueError(f"tasks do not have clean replay candidates: {blocked}")
    return selected


def _rebind_url(value: str, site_access_host: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.port not in {
        port for pair in SITE_PORTS.values() for port in pair
    }:
        return value
    host = f"[{site_access_host}]" if ":" in site_access_host else site_access_host
    netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _rebind_calls(calls: list[dict[str, Any]], site_access_host: str) -> list[dict[str, Any]]:
    rebound = []
    for call in calls:
        item = dict(call)
        if isinstance(item.get("url"), str):
            item["url"] = _rebind_url(item["url"], site_access_host)
        rebound.append(item)
    return rebound


def _run_one(  # noqa: PLR0913
    *,
    task_seed: dict[str, Any],
    engine: str,
    endpoint: str,
    container: Any,
    benchmark: Any,
    cowagent_repo: Path,
    output_dir: Path,
    sample_interval_ms: int,
) -> dict[str, Any]:
    task_id = int(task_seed["task_id"])
    candidate = _candidate(task_seed)
    official_task = benchmark.get_task(task_id)
    sites = tuple(official_task.sites)
    start_urls = list(benchmark.config.render_url(list(official_task.start_urls), list(sites)))
    calls = _rebind_calls(candidate["tool_calls"], _container_host_gateway(container))
    attempt_dir = output_dir / engine / str(task_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    har_path = attempt_dir / "network.har"
    trace_path = attempt_dir / "trace.json"
    evaluation_path = attempt_dir / "evaluation.json"
    screenshot_path = attempt_dir / "failure.png"

    if str(cowagent_repo) not in sys.path:
        sys.path.insert(0, str(cowagent_repo))
    from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

    tool = BrowserTool(
        config={
            "cdp_endpoint": endpoint,
            "headless": True,
            "viewport": {"width": 1280, "height": 900},
            "startup_timeout": 90,
        }
    )
    _allow_official_container_hostname(tool)
    resources = DockerStatsResources(container)
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    before = None
    resource_started = None
    recorder = CdpHarRecorder()
    records: list[dict[str, Any]] = []
    timings: dict[str, float] = {}
    process_status = "fail"
    evaluator_status = "not_run"
    error = ""
    started = time.perf_counter()
    sampler_started = False
    try:
        prepare_started = time.perf_counter()
        _prepare_sites(sites)
        timings["environment_prepare_ms"] = (time.perf_counter() - prepare_started) * 1000

        setup_started = time.perf_counter()
        service = tool._get_service()
        service._submit(lambda: None)
        readiness_retries = _login_context(service, sites, benchmark, start_urls)
        service._submit(lambda: recorder.start(service._context))
        timings["browser_session_setup_ms"] = (time.perf_counter() - setup_started) * 1000

        before = resources.counters()
        resource_started = time.perf_counter()
        sampler.start()
        sampler_started = True
        replay_started = time.perf_counter()
        for index, arguments in enumerate(calls, start=1):
            call_started = time.perf_counter()
            result = tool.execute(arguments)
            record = {
                "index": index,
                "arguments": arguments,
                "status": result.status,
                "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                "result": result.result,
            }
            records.append(record)
            if result.status != "success":
                raise RuntimeError(f"step {index} failed: {result.result}")
        timings["trajectory_replay_ms"] = (time.perf_counter() - replay_started) * 1000
        process_status = "pass"

        finalize_started = time.perf_counter()
        service._submit(
            lambda: (
                service._page.wait_for_timeout(500),
                recorder.stop(service._context, har_path),
            )
        )
        timings["evidence_finalize_ms"] = (time.perf_counter() - finalize_started) * 1000

        evaluation_started = time.perf_counter()
        evaluation = benchmark.evaluate_task(
            task_id=task_id,
            agent_response=candidate["recorded_response"],
            network_trace=har_path,
        ).model_dump(mode="json")
        timings["evaluation_ms"] = (time.perf_counter() - evaluation_started) * 1000
        evaluator_status = "pass" if float(evaluation.get("score") or 0) == 1 else "fail"
        evaluation_path.write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        error = traceback.format_exc()
        try:
            service = tool._get_service()
            service._submit(lambda: recorder.stop(service._context, har_path))
            service._submit(lambda: service._page.screenshot(path=str(screenshot_path)))
        except Exception:
            pass
        readiness_retries = locals().get("readiness_retries", 0)
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        if sampler_started:
            sampler.stop()
        after = resources.counters() if before is not None else None
        resource_duration_ms = (
            (time.perf_counter() - resource_started) * 1000 if resource_started is not None else 0.0
        )
        tool.close()

    timings["browser_resource_measurement_ms"] = resource_duration_ms

    trace_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "schema": "lex_webarena_trajectory_replay_attempt/1",
        "engine": engine,
        "task_id": task_id,
        "source_candidate_id": candidate["id"],
        "source_model": candidate["method"]["model"],
        "source_engine": candidate["method"]["engine"],
        "action_count": len(calls),
        "completed_action_count": sum(record["status"] == "success" for record in records),
        "process_status": process_status,
        "evaluator_status": evaluator_status,
        "status": "pass" if process_status == evaluator_status == "pass" else "fail",
        "duration_ms": round(duration_ms, 3),
        "timings": {key: round(value, 3) for key, value in timings.items()},
        "browser_session_readiness_retries": readiness_retries,
        "resource": (
            _resource_summary(resources, sampler, before, after, resource_duration_ms)
            if before is not None and after is not None and sampler.samples
            else {}
        ),
        "error": error,
        "paths": {
            "trace": str(trace_path),
            "network_har": str(har_path),
            "evaluation": str(evaluation_path),
            "failure_screenshot": str(screenshot_path) if screenshot_path.is_file() else None,
        },
    }


def run_canary_replay(  # noqa: PLR0913
    *,
    seed_manifest_path: str | Path,
    task_ids: list[int],
    engines: list[str],
    output_dir: str | Path,
    cowagent_repo: str | Path,
    cpus: float = 2.0,
    memory: str = "2g",
    sample_interval_ms: int = 100,
) -> dict[str, Any]:
    seeds = json.loads(Path(seed_manifest_path).read_text(encoding="utf-8"))
    tasks = select_tasks(seeds, task_ids)
    unknown_engines = sorted(set(engines) - set(ENGINE_IMAGES))
    if unknown_engines:
        raise ValueError(f"unsupported replay engines: {unknown_engines}")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    cowagent = Path(cowagent_repo).expanduser().resolve()
    if not (cowagent / "agent/tools/browser/browser_tool.py").is_file():
        raise ValueError("cowagent_repo does not contain BrowserTool")

    client = docker.from_env()
    run_id = uuid.uuid4().hex[:12]
    containers: dict[str, Any] = {}
    networks: dict[str, Any] = {}
    endpoints: dict[str, str] = {}
    attempts: list[dict[str, Any]] = []
    try:
        for engine in engines:
            network = client.networks.create(f"lex-webarena-replay-{run_id}-{engine}")
            networks[engine] = network
            container = _start_browser_container(
                client,
                engine=engine,
                run_id=f"replay-{run_id}",
                cpus=cpus,
                memory=memory,
                network=network.name,
            )
            containers[engine] = container
            container.reload()
            http_endpoint = _published_endpoint(
                container, engine=engine, cdp_port=ENGINE_PORTS[engine]
            )
            endpoints[engine] = _wait_cdp(http_endpoint, container=container)

        gateway_addresses = {
            _container_host_gateway(container) for container in containers.values()
        }
        if len(gateway_addresses) != 1:
            raise RuntimeError(f"browser containers disagree on host gateway: {gateway_addresses}")
        benchmark = _configure_benchmark(hostname=gateway_addresses.pop())
        lock_path = _physical_execution_lock_path()
        for task_index, task in enumerate(tasks):
            order = engines if task_index % 2 == 0 else list(reversed(engines))
            for engine in order:
                with _physical_execution_lock(lock_path):
                    attempt = _run_one(
                        task_seed=task,
                        engine=engine,
                        endpoint=endpoints[engine],
                        container=containers[engine],
                        benchmark=benchmark,
                        cowagent_repo=cowagent,
                        output_dir=output,
                        sample_interval_ms=sample_interval_ms,
                    )
                attempts.append(attempt)
                print(
                    json.dumps(
                        {
                            "task_id": attempt["task_id"],
                            "engine": attempt["engine"],
                            "process_status": attempt["process_status"],
                            "evaluator_status": attempt["evaluator_status"],
                        }
                    ),
                    flush=True,
                )
    finally:
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

    result = {
        "schema": "lex_webarena_trajectory_replay_canary/1",
        "run_id": run_id,
        "source_seed_manifest": str(Path(seed_manifest_path).resolve()),
        "task_ids": task_ids,
        "engines": engines,
        "attempts": attempts,
    }
    (output / "canary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
