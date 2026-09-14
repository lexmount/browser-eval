"""Model-free replay of fixed Browser Use trajectories on WebArena-Verified."""

from __future__ import annotations

import hashlib
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
    _cdp_metadata,
    _configure_benchmark,
    _container_host_gateway,
    _docker_image_metadata,
    _login_context,
    _physical_execution_lock,
    _physical_execution_lock_path,
    _prepare_sites,
    _require_clean_git_checkout,
    _resource_summary,
    _site_container_manager,
)
from harness.trajectory_replay.metrics import DockerStatsResources, ResourceSampler
from harness.trajectory_replay.runner import (
    ENGINE_IMAGES,
    ENGINE_PORTS,
    _published_endpoint,
    _start_browser_container,
    _wait_cdp,
)
from harness.trajectory_replay.webarena_corrections import (
    apply_correction,
    check_replay_assertions,
    evaluate_corrected_contract,
    load_corrections,
)
from harness.trajectory_replay.webarena_plan import (
    load_replay_plan,
    replay_calls_binding_sha256,
    replay_site_hosts,
)

RECEIPT_SCHEMA = "lex_webarena_trajectory_replay_suite/1"

_CLICK_DISPATCHED_NAVIGATION_TIMEOUT_MARKERS = (
    "Click failed: Page.click: Timeout",
    "performing click action",
    "click action done",
    "waiting for scheduled navigations to finish",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid WebArena environment URL: {url}")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _evidence_file(path: Path) -> dict[str, str] | None:
    return {"path": str(path), "sha256": _sha256(path)} if path.is_file() else None


def _click_was_dispatched_before_navigation_timeout(
    arguments: dict[str, Any], result_status: str, result_text: str
) -> bool:
    """Separate a completed click from Playwright's later navigation wait timeout.

    Replaying the click would risk applying a mutation twice.  The following
    trajectory step and official evaluator remain responsible for proving that
    the resulting page state is usable and correct.
    """

    return (
        arguments.get("action") == "click"
        and result_status != "success"
        and all(marker in result_text for marker in _CLICK_DISPATCHED_NAVIGATION_TIMEOUT_MARKERS)
    )


def _execute_replay_call(tool: Any, arguments: dict[str, Any]) -> tuple[str, str]:
    """Execute one call without changing the semantics of a compiled snapshot ref."""

    replay_mode = arguments.get("replay_mode")
    public_arguments = {key: value for key, value in arguments.items() if key != "replay_mode"}
    if replay_mode != "snapshot_ref_semantics":
        result = tool.execute(public_arguments)
        return str(result.status), str(result.result)

    action = str(public_arguments.get("action") or "")
    selector = str(public_arguments.get("selector") or "")
    if action not in {"click", "fill", "select"} or not selector:
        return "error", "Invalid compiled snapshot-ref replay call"
    service = tool._get_service()

    try:
        def execute() -> None:
            page = service._page
            locator = page.locator(selector).first
            if action == "click":
                locator.evaluate("element => element.click()")
                page.wait_for_timeout(500)
            elif action == "fill":
                locator.evaluate("element => { element.focus(); element.value = ''; }")
                page.keyboard.type(str(public_arguments.get("text", "")))
            else:
                value = str(public_arguments.get("value", ""))
                locator.evaluate(
                    "(element, value) => { if (element.tagName.toLowerCase() !== 'select') "
                    "throw new Error('target is not a select element'); element.value = value; "
                    "element.dispatchEvent(new Event('change', {bubbles: true})); }",
                    value,
                )

        service._submit(execute)
    except Exception as exc:
        return "error", f"{action.capitalize()} failed: {exc}"
    return "success", f"{action.capitalize()} completed with snapshot-ref semantics."


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


def select_methods(plan: dict[str, Any], engines: list[str]) -> list[dict[str, Any]]:
    """Return only methods registered for this formal or diagnostic engine scope."""

    selected = [method for method in plan["methods"] if str(method["engine"]) in engines]
    selected_engines = [str(method["engine"]) for method in selected]
    if selected_engines != engines:
        raise ValueError("replay methods do not match the selected engine scope")
    return selected


def validate_qualification_dataset(plan: dict[str, Any], path: str | Path) -> str:
    """Bind a formal comparison to the complete pre-qualified trajectory set."""

    dataset_path = Path(path).expanduser().resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if dataset.get("schema") != "lex_webarena_verified_trajectory_dataset/1":
        raise ValueError("formal replay requires the current qualified trajectory dataset")
    provenance = dataset.get("provenance") or {}
    if provenance.get("seed_manifest_sha256") != plan["seed_manifest_sha256"]:
        raise ValueError("qualified trajectories differ from the frozen replay seeds")
    if provenance.get("profile_sha256") != plan["profile_sha256"]:
        raise ValueError("qualified trajectories differ from the frozen replay profile")
    policy = dataset.get("promotion_policy") or {}
    if policy.get("qualification_browser") != plan["qualification_engine"]:
        raise ValueError("qualified trajectories use a different reference browser")
    trajectories = dataset.get("trajectories") or []
    by_task = {int(item["task_id"]): item for item in trajectories}
    if len(by_task) != len(trajectories) or set(by_task) != set(plan["task_ids"]):
        raise ValueError("qualified trajectories do not cover the frozen task set")
    seed_tasks = {
        int(item["task_id"]): item for item in plan["seed_manifest"].get("tasks") or []
    }
    for task_id, trajectory in by_task.items():
        if trajectory.get("tool_calls") != _candidate(seed_tasks[task_id]).get("tool_calls"):
            raise ValueError(f"qualified trajectory differs from task {task_id} canonical input")
    return _sha256(dataset_path)


def _rebind_url(
    value: str, site_access_host: str, trusted_source_site_hosts: set[str]
) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.port not in {port for pair in SITE_PORTS.values() for port in pair}
        or parsed.hostname not in trusted_source_site_hosts
    ):
        return value
    host = f"[{site_access_host}]" if ":" in site_access_host else site_access_host
    netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _rebind_calls(
    calls: list[dict[str, Any]],
    site_access_host: str,
    trusted_source_site_hosts: set[str],
) -> list[dict[str, Any]]:
    rebound = []
    for call in calls:
        item = dict(call)
        if isinstance(item.get("url"), str):
            item["url"] = _rebind_url(
                item["url"], site_access_host, trusted_source_site_hosts
            )
        rebound.append(item)
    return rebound


def _clear_browser_state(service: Any, origins: set[str]) -> None:
    """Fail closed unless all persistent browser state is cleared before a task."""

    def clear() -> None:
        context = service._context
        page = service._page
        if context is None or page is None:
            raise RuntimeError("browser context is not ready for state reset")
        context.clear_cookies()
        context.clear_permissions()
        cdp = context.new_cdp_session(page)
        try:
            cdp.send("Network.enable")
            cdp.send("Network.clearBrowserCache")
            cdp.send("Network.clearBrowserCookies")
            for origin in sorted(origins):
                cdp.send(
                    "Storage.clearDataForOrigin",
                    {"origin": origin, "storageTypes": "all"},
                )
            page.goto("about:blank", wait_until="load", timeout=10_000)
            cdp.send("Page.resetNavigationHistory")
        finally:
            cdp.detach()

    service._submit(clear)


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
    physical_execution: int,
    trusted_source_site_hosts: set[str],
) -> dict[str, Any]:
    task_id = int(task_seed["task_id"])
    candidate = _candidate(task_seed)
    official_task = benchmark.get_task(task_id)
    correction_catalog = load_corrections()
    corrected_contract = (
        apply_correction(official_task, correction_catalog)
        if task_id in correction_catalog.records
        else None
    )
    sites = tuple(official_task.sites)
    start_urls = list(benchmark.config.render_url(list(official_task.start_urls), list(sites)))
    site_access_host = _container_host_gateway(container)
    calls = _rebind_calls(
        candidate["tool_calls"], site_access_host, trusted_source_site_hosts
    )
    source_site_hosts = replay_site_hosts(candidate["tool_calls"])
    attempt_dir = output_dir / engine / str(task_id) / f"physical-{physical_execution}"
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
    replay_assertions: tuple[dict[str, Any], ...] = ()
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
        origins = {
            _origin(benchmark.config.get_environment(site).active_url) for site in SITE_PORTS
        }
        _clear_browser_state(service, origins)
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
            result_status, result_text = _execute_replay_call(tool, arguments)
            dispatched_before_timeout = _click_was_dispatched_before_navigation_timeout(
                arguments, result_status, result_text
            )
            record = {
                "index": index,
                "arguments": arguments,
                "status": "success" if dispatched_before_timeout else result_status,
                "tool_status": result_status,
                "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                "result": result_text,
            }
            if dispatched_before_timeout:
                record["recovery"] = {
                    "classification": "click_dispatched_navigation_wait_timeout",
                    "action_retried": False,
                }
            records.append(record)
            if result_status != "success" and not dispatched_before_timeout:
                raise RuntimeError(f"step {index} failed: {result_text}")
        timings["trajectory_replay_ms"] = (time.perf_counter() - replay_started) * 1000
        process_status = "pass"

        if corrected_contract is not None and corrected_contract.replay_assertions:
            selectors = {
                selector
                for assertion in corrected_contract.replay_assertions
                for selector in (assertion.get("fields") or {})
            }
            page_state = service._submit(
                lambda: {
                    "url": service._page.url,
                    "text": service._page.locator("body").inner_text(),
                    "fields": service._page.evaluate(
                        "selectors => Object.fromEntries(selectors.map(selector => "
                        "[selector, document.querySelector(selector)?.value ?? null]))",
                        sorted(selectors),
                    ),
                }
            )
            replay_assertions = check_replay_assertions(
                corrected_contract,
                page_url=page_state["url"],
                page_text=page_state["text"],
                field_values=page_state["fields"],
            )

        finalize_started = time.perf_counter()
        service._submit(
            lambda: (
                service._page.wait_for_timeout(500),
                recorder.stop(service._context, har_path),
            )
        )
        timings["evidence_finalize_ms"] = (time.perf_counter() - finalize_started) * 1000

        evaluation_started = time.perf_counter()
        evaluation_result = (
            evaluate_corrected_contract(
                benchmark,
                corrected_contract,
                agent_response=candidate["recorded_response"],
                network_trace=har_path,
            )
            if corrected_contract is not None
            else benchmark.evaluate_task(
                task_id=task_id,
                agent_response=candidate["recorded_response"],
                network_trace=har_path,
            )
        )
        evaluation = evaluation_result.model_dump(mode="json")
        if corrected_contract is not None:
            evaluation["replay_contract_correction"] = {
                "correction_id": corrected_contract.correction_id,
                "source_sha256": corrected_contract.correction_source_sha256,
                "original_task_sha256": corrected_contract.original_task_sha256,
                "corrected_task_sha256": corrected_contract.corrected_task_sha256,
                "rationale": corrected_contract.rationale,
            }
            evaluation["replay_assertions"] = list(replay_assertions)
        timings["evaluation_ms"] = (time.perf_counter() - evaluation_started) * 1000
        evaluator_status = (
            "pass"
            if float(evaluation.get("score") or 0) == 1
            and all(item.get("status") == "pass" for item in replay_assertions)
            else "fail"
        )
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
        "physical_execution": physical_execution,
        "source_candidate_id": candidate["id"],
        "source_tool_calls_sha256": _json_sha256(candidate["tool_calls"]),
        "executed_tool_calls_sha256": _json_sha256(calls),
        "source_site_hosts": sorted(source_site_hosts),
        "runtime_site_host": site_access_host,
        "replay_calls_binding_sha256": replay_calls_binding_sha256(
            calls, allowed_hosts={site_access_host}
        ),
        "evaluation_input_sha256": _json_sha256(candidate["recorded_response"]),
        "source_model": (candidate.get("method") or {}).get("model"),
        "source_engine": (candidate.get("method") or {}).get("engine"),
        "source_kind": (candidate.get("source") or {}).get("kind", "browser_use_execution"),
        "action_count": len(calls),
        "completed_action_count": sum(record["status"] == "success" for record in records),
        "process_status": process_status,
        "evaluator_status": evaluator_status,
        "replay_assertions": list(replay_assertions),
        "contract_correction_id": (
            corrected_contract.correction_id if corrected_contract is not None else None
        ),
        "contract_correction_source_sha256": (
            corrected_contract.correction_source_sha256 if corrected_contract is not None else None
        ),
        "corrected_task_sha256": (
            corrected_contract.corrected_task_sha256 if corrected_contract is not None else None
        ),
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
        "evidence": {
            "trace": _evidence_file(trace_path),
            "network_har": _evidence_file(har_path),
            "evaluation": _evidence_file(evaluation_path),
            "failure_screenshot": _evidence_file(screenshot_path),
        },
        "paths": {
            "trace": str(trace_path),
            "network_har": str(har_path),
            "evaluation": str(evaluation_path),
            "failure_screenshot": str(screenshot_path) if screenshot_path.is_file() else None,
        },
    }


def _browser_context_needs_restart(attempt: dict[str, Any]) -> bool:
    """Return whether setup failed before replay because the browser context is unusable."""
    return (
        attempt.get("process_status") == "fail"
        and attempt.get("completed_action_count") == 0
        and "BrowserContextNotLoaded" in str(attempt.get("error") or "")
    )


def _stop_all_sites() -> None:
    """Stop the fixed-port site stack before another method can acquire it."""

    for site in SITE_PORTS:
        manager = _site_container_manager(site)
        if manager.is_running():
            manager.stop()


def _teardown_method(client: Any, run_id: str, engine: str) -> None:
    """Remove every runtime object for a method and fail if isolation is incomplete."""

    errors = []
    labels = [
        f"lex.browser-use-eval.run=replay-{run_id}",
        f"lex.browser-use-eval.engine={engine}",
    ]
    for container in client.containers.list(all=True, filters={"label": labels}):
        try:
            container.remove(force=True)
        except Exception as exc:
            errors.append(f"browser container {container.name}: {exc}")
    network_name = f"lex-webarena-replay-{run_id}-{engine}"
    for network in client.networks.list(names=[network_name]):
        try:
            network.remove()
        except Exception as exc:
            errors.append(f"browser network {network.name}: {exc}")
    try:
        _stop_all_sites()
    except Exception as exc:
        errors.append(f"site stack: {exc}")
    if errors:
        raise RuntimeError("method teardown failed: " + "; ".join(errors))


def _remove_orphan_replay_runtimes(client: Any, current_run_id: str) -> None:
    """Remove stale replay containers and networks while the global stack lock is held."""

    errors = []
    for container in client.containers.list(
        all=True, filters={"label": "lex.browser-use-eval.run"}
    ):
        run_label = str((container.labels or {}).get("lex.browser-use-eval.run") or "")
        if run_label.startswith("replay-") and run_label != f"replay-{current_run_id}":
            try:
                container.remove(force=True)
            except Exception as exc:
                errors.append(f"orphan container {container.name}: {exc}")
    for network in client.networks.list():
        if network.name.startswith("lex-webarena-replay-") and current_run_id not in network.name:
            try:
                network.remove()
            except Exception as exc:
                errors.append(f"orphan network {network.name}: {exc}")
    if errors:
        raise RuntimeError("orphan replay cleanup failed: " + "; ".join(errors))


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _next_physical_execution(output: Path, engine: str, task_id: int) -> int:
    task_dir = output / engine / str(task_id)
    existing = {
        int(path.name.removeprefix("physical-"))
        for path in task_dir.glob("physical-*")
        if path.is_dir() and path.name.removeprefix("physical-").isdigit()
    }
    return max(existing, default=0) + 1


def _start_physical_execution(
    receipt: dict[str, Any], *, engine: str, task_id: int, physical_execution: int
) -> dict[str, Any]:
    entry = {
        "engine": engine,
        "task_id": task_id,
        "physical_execution": physical_execution,
        "status": "running",
        "started_at_unix_ms": round(time.time() * 1000),
    }
    receipt.setdefault("physical_executions", []).append(entry)
    return entry


def _finish_physical_execution(entry: dict[str, Any], attempt: dict[str, Any]) -> None:
    entry.update(
        {
            "status": "complete",
            "finished_at_unix_ms": round(time.time() * 1000),
            "attempt_status": attempt["status"],
            "duration_ms": attempt["duration_ms"],
            "evidence": attempt["evidence"],
        }
    )


def _validated_resume(
    receipt_path: Path,
    *,
    plan: dict[str, Any],
    task_ids: list[int],
    engines: list[str],
    scope: str,
    runtime: dict[str, Any],
    qualification_dataset_sha256: str | None = None,
) -> tuple[
    str,
    list[dict[str, Any]],
    dict[str, bool],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    if not receipt_path.is_file():
        return uuid.uuid4().hex[:12], [], {engine: False for engine in engines}, [], []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected = {
        "schema": RECEIPT_SCHEMA,
        "execution_scope": scope,
        "profile_sha256": plan["profile_sha256"],
        "source_seed_manifest_sha256": plan["seed_manifest_sha256"],
        "qualification_dataset_sha256": qualification_dataset_sha256,
        "task_ids": task_ids,
        "engines": engines,
        "runtime": runtime,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"existing replay receipt differs on {key}")
    attempts = list(receipt.get("attempts") or [])
    canonical_ids = {
        int(task["task_id"]): str(task["canonical_candidate_id"])
        for task in plan["seed_manifest"].get("tasks") or []
    }
    allowed_keys = {(engine, task_id) for engine in engines for task_id in task_ids}
    keys = []
    for item in attempts:
        if item.get("schema") != "lex_webarena_trajectory_replay_attempt/1":
            raise ValueError("existing replay receipt contains an invalid attempt schema")
        key = (str(item.get("engine")), int(item.get("task_id")))
        if key not in allowed_keys:
            raise ValueError("existing replay receipt contains an attempt outside the frozen plan")
        if item.get("source_candidate_id") != canonical_ids[key[1]]:
            raise ValueError("existing replay receipt contains a non-canonical trajectory")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ValueError("existing replay receipt contains duplicate method/task attempts")
    method_teardown = receipt.get("method_teardown") or {}
    if set(method_teardown) != set(engines) or any(
        not isinstance(value, bool) for value in method_teardown.values()
    ):
        raise ValueError("existing replay receipt has invalid method teardown state")
    physical_executions = list(receipt.get("physical_executions") or [])
    if any(item.get("status") != "complete" for item in physical_executions):
        raise ValueError(
            "existing replay receipt contains an interrupted physical execution; "
            "restart the complete formal scope in a new output directory"
        )
    physical_keys = [
        (str(item.get("engine")), int(item.get("task_id")), int(item.get("physical_execution")))
        for item in physical_executions
    ]
    if len(physical_keys) != len(set(physical_keys)):
        raise ValueError("existing replay receipt contains duplicate physical executions")
    physical_by_logical: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for item in physical_executions:
        key = (str(item.get("engine")), int(item.get("task_id")))
        if key not in allowed_keys:
            raise ValueError(
                "existing replay receipt contains a physical execution outside the plan"
            )
        physical_by_logical.setdefault(key, []).append(item)
    attempts_by_key = {
        (str(item["engine"]), int(item["task_id"])): item for item in attempts
    }
    if set(physical_by_logical) != set(attempts_by_key):
        raise ValueError("existing replay receipt has an unbound physical execution")
    for key, attempt in attempts_by_key.items():
        physical = physical_by_logical[key]
        if (
            len(physical) != int(attempt.get("physical_execution_count") or 1)
            or max(int(item["physical_execution"]) for item in physical)
            != int(attempt["physical_execution"])
        ):
            raise ValueError("existing replay receipt has inconsistent physical execution history")
    sessions = list(receipt.get("sessions") or [])
    session_ids = [str(session.get("id") or "") for session in sessions]
    if len(session_ids) != len(set(session_ids)) or any(not item for item in session_ids):
        raise ValueError("existing replay receipt has invalid browser sessions")
    sessions_by_id = {str(session["id"]): session for session in sessions}
    for attempt in attempts:
        session = sessions_by_id.get(str(attempt.get("browser_session_id") or ""))
        if (
            session is None
            or session.get("engine") != attempt.get("engine")
            or int(attempt["task_id"]) not in session.get("task_ids", [])
        ):
            raise ValueError("existing replay attempt is not bound to a browser session")
    return (
        str(receipt["run_id"]),
        attempts,
        dict(method_teardown),
        sessions,
        physical_executions,
    )


def run_replay_suite(  # noqa: PLR0913
    *,
    profile_path: str | Path,
    seed_manifest_path: str | Path,
    output_dir: str | Path,
    cowagent_repo: str | Path,
    diagnostic_task_ids: list[int] | None = None,
    diagnostic_engines: list[str] | None = None,
    qualification_engine: str | None = None,
    qualified_dataset_path: str | Path | None = None,
) -> dict[str, Any]:
    plan = load_replay_plan(profile_path, seed_manifest_path)
    seeds = plan["seed_manifest"]
    task_ids = list(plan["task_ids"] if diagnostic_task_ids is None else diagnostic_task_ids)
    tasks = select_tasks(seeds, task_ids)
    engines = list(plan["engines"])
    if qualification_engine is not None:
        if diagnostic_task_ids is not None or diagnostic_engines is not None:
            raise ValueError("qualification and diagnostic scopes cannot be combined")
        if qualification_engine != plan["qualification_engine"]:
            raise ValueError("qualification engine differs from the frozen reference engine")
        engines = [qualification_engine]
        scope = "qualification"
    elif diagnostic_engines is not None:
        unknown_engines = sorted(set(diagnostic_engines) - set(engines))
        if unknown_engines:
            raise ValueError(f"diagnostic engines are not registered: {unknown_engines}")
        engines = [engine for engine in engines if engine in set(diagnostic_engines)]
        if not engines:
            raise ValueError("diagnostic engine selection is empty")
        scope = "diagnostic"
    else:
        scope = "formal" if diagnostic_task_ids is None else "diagnostic"
    qualification_dataset_sha256 = None
    if scope == "formal":
        if qualified_dataset_path is None:
            raise ValueError("formal replay requires a complete qualified trajectory dataset")
        qualification_dataset_sha256 = validate_qualification_dataset(
            plan, qualified_dataset_path
        )
    elif qualified_dataset_path is not None:
        raise ValueError("a qualified trajectory dataset is only accepted for formal replay")
    resources = plan["profile"].get("resources") or {}
    cpus = float(resources["cpus"])
    memory = str(resources["memory"])
    sample_interval_ms = int(resources["sample_interval_ms"])
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt_path = output / "receipt.json"
    if any(output.iterdir()) and not receipt_path.is_file():
        raise ValueError("replay output directory is not empty and has no resumable receipt")
    cowagent = Path(cowagent_repo).expanduser().resolve()
    if not (cowagent / "agent/tools/browser/browser_tool.py").is_file():
        raise ValueError("cowagent_repo does not contain BrowserTool")

    repo_root = Path(__file__).resolve().parents[2]
    browser_eval_commit = _require_clean_git_checkout(repo_root)
    cowagent_commit = _require_clean_git_checkout(cowagent)
    client = docker.from_env()
    browser_images = {
        engine: _docker_image_metadata(client.images.get(ENGINE_IMAGES[engine]))
        for engine in engines
    }
    site_images = {}
    for site in SITE_PORTS:
        reference = _site_container_manager(site).config.docker_img
        site_images[site.value] = {
            "reference": reference,
            **_docker_image_metadata(client.images.get(reference)),
        }
    runtime = {
        "browser_eval_commit": browser_eval_commit,
        "cowagent_commit": cowagent_commit,
        "resources": {
            "cpus": cpus,
            "memory": memory,
            "sample_interval_ms": sample_interval_ms,
        },
        "browser_images": browser_images,
        "site_images": site_images,
    }
    run_id, attempts, method_teardown, sessions, physical_executions = _validated_resume(
        receipt_path,
        plan=plan,
        task_ids=task_ids,
        engines=engines,
        scope=scope,
        runtime=runtime,
        qualification_dataset_sha256=qualification_dataset_sha256,
    )
    completed = {(str(item["engine"]), int(item["task_id"])) for item in attempts}
    lock_path = _physical_execution_lock_path()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "run_id": run_id,
        "execution_scope": scope,
        "status": "running",
        "profile": plan["profile_path"],
        "profile_sha256": plan["profile_sha256"],
        "source_seed_manifest": plan["seed_manifest_path"],
        "source_seed_manifest_sha256": plan["seed_manifest_sha256"],
        "qualification_dataset_sha256": qualification_dataset_sha256,
        "task_ids": task_ids,
        "engines": engines,
        "runtime": runtime,
        "method_teardown": method_teardown,
        "sessions": sessions,
        "physical_executions": physical_executions,
        "isolation": {
            **plan["profile"]["execution"],
            "physical_execution_lock_namespace": lock_path.stem,
        },
        "attempts": attempts,
    }
    _write_receipt(receipt_path, receipt)
    with _physical_execution_lock(lock_path) as lock_wait_ms:
        _remove_orphan_replay_runtimes(client, run_id)
        for engine in engines:
            _teardown_method(client, run_id, engine)
            method_teardown[engine] = True
        receipt["method_teardown"] = method_teardown
        receipt["isolation"]["lock_wait_ms"] = round(lock_wait_ms, 3)
        _write_receipt(receipt_path, receipt)
        for method in select_methods(plan, engines):
            engine = str(method["engine"])
            pending = [task for task in tasks if (engine, int(task["task_id"])) not in completed]
            if not pending:
                continue
            network = None
            container = None
            try:
                _stop_all_sites()
                method_teardown[engine] = False
                receipt["method_teardown"] = method_teardown
                _write_receipt(receipt_path, receipt)
                network = client.networks.create(f"lex-webarena-replay-{run_id}-{engine}")
                container = _start_browser_container(
                    client,
                    engine=engine,
                    run_id=f"replay-{run_id}",
                    cpus=cpus,
                    memory=memory,
                    network=network.name,
                )
                container.reload()
                http_endpoint = _published_endpoint(
                    container, engine=engine, cdp_port=ENGINE_PORTS[engine]
                )
                endpoint = _wait_cdp(http_endpoint, container=container)
                benchmark = _configure_benchmark(hostname=_container_host_gateway(container))
                session = {
                    "id": uuid.uuid4().hex[:12],
                    "engine": engine,
                    "container_id": container.id,
                    "browser_image": _docker_image_metadata(container.image),
                    "browser": _cdp_metadata(http_endpoint),
                    "site_access_host": _container_host_gateway(container),
                    "task_ids": [],
                }
                sessions.append(session)
                receipt["sessions"] = sessions
                _write_receipt(receipt_path, receipt)
                for task in pending:
                    physical_execution = _next_physical_execution(
                        output, engine, int(task["task_id"])
                    )
                    physical_entry = _start_physical_execution(
                        receipt,
                        engine=engine,
                        task_id=int(task["task_id"]),
                        physical_execution=physical_execution,
                    )
                    _write_receipt(receipt_path, receipt)
                    attempt = _run_one(
                        task_seed=task,
                        engine=engine,
                        endpoint=endpoint,
                        container=container,
                        benchmark=benchmark,
                        cowagent_repo=cowagent,
                        output_dir=output,
                        sample_interval_ms=sample_interval_ms,
                        physical_execution=physical_execution,
                        trusted_source_site_hosts=plan["trusted_source_site_hosts"],
                    )
                    attempt["browser_session_id"] = session["id"]
                    _finish_physical_execution(physical_entry, attempt)
                    _write_receipt(receipt_path, receipt)
                    if _browser_context_needs_restart(attempt):
                        # A long-lived Moli process can leave its default context permanently
                        # unloaded. Waiting on that same context cannot repair it, so restart
                        # the browser container and give this pre-action setup one opportunity.
                        container.restart()
                        container.reload()
                        http_endpoint = _published_endpoint(
                            container, engine=engine, cdp_port=ENGINE_PORTS[engine]
                        )
                        endpoint = _wait_cdp(http_endpoint, container=container)
                        prior_setup_attempt = attempt
                        session = {
                            "id": uuid.uuid4().hex[:12],
                            "engine": engine,
                            "container_id": container.id,
                            "browser_image": _docker_image_metadata(container.image),
                            "browser": _cdp_metadata(http_endpoint),
                            "site_access_host": _container_host_gateway(container),
                            "task_ids": [],
                        }
                        sessions.append(session)
                        receipt["sessions"] = sessions
                        retry_entry = _start_physical_execution(
                            receipt,
                            engine=engine,
                            task_id=int(task["task_id"]),
                            physical_execution=physical_execution + 1,
                        )
                        _write_receipt(receipt_path, receipt)
                        attempt = _run_one(
                            task_seed=task,
                            engine=engine,
                            endpoint=endpoint,
                            container=container,
                            benchmark=benchmark,
                            cowagent_repo=cowagent,
                            output_dir=output,
                            sample_interval_ms=sample_interval_ms,
                            physical_execution=physical_execution + 1,
                            trusted_source_site_hosts=plan["trusted_source_site_hosts"],
                        )
                        attempt["browser_session_id"] = session["id"]
                        attempt["prior_setup_attempts"] = [prior_setup_attempt]
                        attempt["physical_execution_count"] = 2
                        attempt["duration_ms"] = round(
                            float(prior_setup_attempt["duration_ms"])
                            + float(attempt["duration_ms"]),
                            3,
                        )
                        _finish_physical_execution(retry_entry, attempt)
                        _write_receipt(receipt_path, receipt)
                    attempts.append(attempt)
                    session["task_ids"].append(int(task["task_id"]))
                    completed.add((engine, int(task["task_id"])))
                    receipt["attempts"] = attempts
                    _write_receipt(receipt_path, receipt)
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
                _teardown_method(client, run_id, engine)
                method_teardown[engine] = True
                receipt["method_teardown"] = method_teardown
                _write_receipt(receipt_path, receipt)

    expected = {(engine, task_id) for engine in engines for task_id in task_ids}
    actual = {(str(item["engine"]), int(item["task_id"])) for item in attempts}
    if actual != expected or len(attempts) != len(expected):
        raise RuntimeError("formal replay did not produce the complete method-task matrix")
    if not all(method_teardown.values()):
        raise RuntimeError("formal replay did not complete method teardown")
    receipt["status"] = "complete"
    receipt["attempts"] = attempts
    _write_receipt(receipt_path, receipt)
    return receipt
