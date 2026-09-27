"""Model-free replay of fixed Browser Use trajectories on WebArena-Verified."""

from __future__ import annotations

import hashlib
import json
import subprocess
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
    _browser_session_contract,
    _browser_tool_config,
    _cdp_metadata,
    _configure_benchmark,
    _container_host_gateway,
    _docker_daemon_lock_namespace,
    _docker_image_metadata,
    _login_context,
    _physical_execution_lock,
    _physical_execution_lock_path,
    _prepare_browser_viewport,
    _prepare_sites,
    _require_clean_git_checkout,
    _resource_summary,
    _site_container_manager,
    _verify_session_viewport,
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

_STATE_CLEAR_NAVIGATION_SETTLE_TIMEOUT_MS = 1_500
_STATE_CLEAR_NAVIGATION_GRACE_MS = 300
_STATE_CLEAR_MAX_ATTEMPTS = 3


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


def _execute_replay_call(
    tool: Any, arguments: dict[str, Any], *, diagnostics: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Execute one call without changing the semantics of a compiled snapshot ref."""

    replay_mode = arguments.get("replay_mode")
    public_arguments = {key: value for key, value in arguments.items() if key != "replay_mode"}
    if public_arguments.get("action") == "wait" and "seconds" in public_arguments:
        if "timeout" in public_arguments:
            return "error", "Wait call cannot declare both seconds and timeout"
        seconds = public_arguments.pop("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            return "error", "Wait seconds must be a positive number"
        public_arguments["timeout"] = round(seconds * 1000)
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
            layout_observer = getattr(service, "layout_observer", None)
            timeout_ms = int(public_arguments.get("timeout_ms", 60_000))
            deadline = time.monotonic() + timeout_ms / 1000

            def remaining() -> int:
                budget = int((deadline - time.monotonic()) * 1000)
                if budget < 1:
                    raise TimeoutError("compiled snapshot-ref action timed out")
                return budget

            if layout_observer is not None:
                layout_observer(page, remaining())
            locator = None
            if layout_observer is not None and selector.endswith(":visible"):
                stable = page.locator(selector.removesuffix(":visible"))
                count = getattr(stable, "count", None)
                if callable(count) and count() == 1:
                    locator = stable.first
                    if not bool(public_arguments.get("force", False)):
                        visible = page.locator(selector)
                        while visible.count() != 1:
                            page.wait_for_timeout(min(100, remaining()))
                            layout_observer(page, remaining())
            if locator is None:
                locator = page.locator(selector).first
            if public_arguments.get("wait_for_enabled"):
                while True:
                    state = locator.evaluate(
                        """element => ({
                            connected: element.isConnected,
                            disabled: Boolean(element.disabled),
                            effectivelyDisabled: element.matches(':disabled'),
                            ariaDisabled: element.getAttribute('aria-disabled')
                        })"""
                    )
                    if (
                        state.get("connected")
                        and not state.get("disabled")
                        and not state.get("effectivelyDisabled")
                        and state.get("ariaDisabled") != "true"
                    ):
                        break
                    page.wait_for_timeout(min(100, remaining()))
                    if layout_observer is not None:
                        layout_observer(page, remaining())
            if action == "click":
                if diagnostics is None:
                    locator.evaluate("element => element.click()")
                else:
                    # Observe and dispatch in one JS turn: a separate probe can
                    # race widget initialization. Do not add actionability waits
                    # or reinterpret a successful DOM call as a successful task.
                    state = locator.evaluate("""element => {
                        const state = {
                            connected: element.isConnected,
                            disabled: Boolean(element.disabled),
                            effectivelyDisabled: element.matches(':disabled'),
                            ariaDisabled: element.getAttribute('aria-disabled')
                        };
                        element.click();
                        return state;
                    }""")
                    diagnostics["click_dispatch_state"] = state
                page.wait_for_timeout(500)
            elif action == "fill":
                text = str(public_arguments.get("text", ""))
                if public_arguments.get("force"):
                    state = locator.evaluate(
                        "(element, expected) => { "
                        "const setter = Object.getOwnPropertyDescriptor("
                        "Object.getPrototypeOf(element), 'value')?.set; "
                        "if (!setter) throw new Error('target has no native value setter'); "
                        "setter.call(element, expected); "
                        "element.dispatchEvent(new Event('input', {bubbles: true})); "
                        "element.dispatchEvent(new Event('change', {bubbles: true})); "
                        "if (element.value !== expected) "
                        "throw new Error('filled value was not committed'); "
                        "return {value: element.value}; }",
                        text,
                    )
                else:
                    platform = locator.evaluate(
                        "element => { element.focus(); return navigator.platform; }"
                    )
                    modifier = (
                        "Meta"
                        if str(platform).startswith(("Mac", "iPhone", "iPad", "iPod"))
                        else "Control"
                    )
                    page.keyboard.press(f"{modifier}+A")
                    page.keyboard.press("Backspace")
                    page.keyboard.insert_text(text)
                    state = locator.evaluate(
                        "(element, expected) => { "
                        "element.dispatchEvent(new Event('input', {bubbles: true})); "
                        "element.dispatchEvent(new Event('change', {bubbles: true})); "
                        "if (element.value !== expected) "
                        "throw new Error('filled value was not committed'); "
                        "return {value: element.value}; }",
                        text,
                    )
                if diagnostics is not None:
                    diagnostics["fill_dispatch_state"] = state
            else:
                value = str(public_arguments.get("value", ""))
                state = locator.evaluate(
                    "(element, value) => { if (element.tagName.toLowerCase() !== 'select') "
                    "throw new Error('target is not a select element'); "
                    "const before = element.value; "
                    "element.value = value; if (element.value !== value) "
                    "throw new Error('select option value was not found'); "
                    "element.dispatchEvent(new Event('input', {bubbles: true})); "
                    "element.dispatchEvent(new Event('change', {bubbles: true})); "
                    "return {before, after: element.value, selectedText: "
                    "element.selectedOptions[0]?.textContent ?? null}; }",
                    value,
                )
                if diagnostics is not None:
                    diagnostics["select_dispatch_state"] = state

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

    from .retrieval_acceptance import digest, require_current_retrieval
    from .webarena_dataset import OBSERVATION_PROTOCOL

    require_current_retrieval(plan)
    dataset_path = Path(path).expanduser().resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if dataset.get("schema") != "lex_webarena_verified_trajectory_dataset/1":
        raise ValueError("formal replay requires the current qualified trajectory dataset")
    provenance = dataset.get("provenance") or {}
    if provenance.get("observation_protocol") != OBSERVATION_PROTOCOL:
        raise ValueError("qualified dataset needs fresh valid-observation qualification")
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
    seed_tasks = {int(item["task_id"]): item for item in plan["seed_manifest"].get("tasks") or []}
    for task_id, trajectory in by_task.items():
        if (
            task_id in plan.get("state_contracts", {})
            and trajectory.get("verification", {}).get("state_contract_sha256")
            != plan["state_contracts"][task_id].definition_sha256
        ):
            raise ValueError("legacy qualification does not prove observed form state")
        if trajectory.get("tool_calls") != _candidate(seed_tasks[task_id]).get("tool_calls"):
            raise ValueError(f"qualified trajectory differs from task {task_id} canonical input")
        if task_id in plan.get("retrieval_task_ids", []):
            verification = trajectory.get("verification", {})
            actual_protocol = verification.get(
                "retrieval_protocol",
                (provenance.get("retrieval_protocols") or {}).get(
                    str(task_id), provenance.get("retrieval_protocol")
                ),
            )
            if actual_protocol != plan["retrieval_contracts"][task_id][
                "schema"
            ] or verification.get("retrieval_contract_sha256") != digest(
                plan["retrieval_contracts"][task_id]
            ):
                raise ValueError("legacy qualification does not prove current-run retrieval")
    return _sha256(dataset_path)


def _rebind_url(value: str, site_access_host: str, trusted_source_site_hosts: set[str]) -> str:
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
            item["url"] = _rebind_url(item["url"], site_access_host, trusted_source_site_hosts)
        rebound.append(item)
    return rebound


def _clear_page_storage(page: Any) -> None:
    script = (
        "async () => {"
        "localStorage.clear(); sessionStorage.clear();"
        "if ('caches' in globalThis) {"
        "for (const key of await caches.keys()) await caches.delete(key);"
        "}"
        "if ('indexedDB' in globalThis && indexedDB.databases) {"
        "for (const db of await indexedDB.databases()) {"
        "if (db.name) indexedDB.deleteDatabase(db.name);"
        "}"
        "}"
        "}"
    )
    for attempt in range(_STATE_CLEAR_MAX_ATTEMPTS):
        try:
            page.evaluate(script)
            return
        except Exception as exc:
            if (
                "execution context was destroyed" not in str(exc).lower()
                or attempt + 1 == _STATE_CLEAR_MAX_ATTEMPTS
            ):
                raise
            page.wait_for_load_state(
                "domcontentloaded", timeout=_STATE_CLEAR_NAVIGATION_SETTLE_TIMEOUT_MS
            )
            page.wait_for_timeout(_STATE_CLEAR_NAVIGATION_GRACE_MS)


def _clear_browser_state(service: Any, origins: set[str]) -> None:
    """Fail closed unless all persistent browser state is cleared before a task."""

    def clear() -> None:
        context = service._context
        page = service._page
        if context is None or page is None:
            raise RuntimeError("browser context is not ready for state reset")
        context.clear_cookies()
        context.clear_permissions()
        supports_network_reset_cdp = getattr(
            service, "_supports_network_reset_cdp", True
        )
        supports_storage_cdp = getattr(service, "_supports_storage_cdp", True)
        cdp = (
            context.new_cdp_session(page)
            if supports_network_reset_cdp or supports_storage_cdp
            else None
        )
        try:
            if supports_network_reset_cdp:
                cdp.send("Network.enable")
                cdp.send("Network.clearBrowserCache")
                cdp.send("Network.clearBrowserCookies")
            if supports_storage_cdp:
                for origin in sorted(origins):
                    cdp.send(
                        "Storage.clearDataForOrigin",
                        {"origin": origin, "storageTypes": "all"},
                    )
            else:
                for origin in sorted(origins):
                    page.goto(origin, wait_until="domcontentloaded", timeout=30_000)
                    _clear_page_storage(page)
            page.goto("about:blank", wait_until="load", timeout=10_000)
            if supports_storage_cdp:
                cdp.send("Page.resetNavigationHistory")
        finally:
            if cdp is not None:
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
    retrieval_contract: dict[str, Any] | None = None,
    state_contract: Any = None,
    run_id: str = "",
    prepare_sites: bool = True,
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
    if state_contract is not None:
        from harness.webarena_state import require_supersession

        if corrected_contract is not None:
            require_supersession(
                state_contract,
                corrected_contract.correction_id,
                corrected_contract.correction_source_sha256,
            )
        if (
            benchmark.config.render_url(state_contract.definition["page_url"], official_task.sites)
            != state_contract.page_url
        ):
            raise ValueError("state contract runtime site differs from frozen profile")
        corrected_contract = None
    sites = tuple(official_task.sites)
    start_urls = list(benchmark.config.render_url(list(official_task.start_urls), list(sites)))
    site_access_host = _container_host_gateway(container)
    calls = _rebind_calls(candidate["tool_calls"], site_access_host, trusted_source_site_hosts)
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

    tool = BrowserTool(config=_browser_tool_config(endpoint))
    _allow_official_container_hostname(tool)
    resources = DockerStatsResources(container)
    sampler = ResourceSampler(resources, interval_ms=sample_interval_ms)
    before = None
    resource_started = None
    recorder = CdpHarRecorder()
    records: list[dict[str, Any]] = []
    from .retrieval_acceptance import PROTOCOL, build_actual_response

    attempt_id = uuid.uuid4().hex
    actual_response = None
    retrieval_acceptance = None
    timings: dict[str, float] = {}
    process_status = "fail"
    evaluator_status = "not_run"
    replay_assertions: tuple[dict[str, Any], ...] = ()
    state_observation = None
    error = ""
    started = time.perf_counter()
    sampler_started = False
    viewport_evidence: dict[str, Any] = {}
    try:
        prepare_started = time.perf_counter()
        if prepare_sites:
            _prepare_sites(sites)
        timings["environment_prepare_ms"] = (time.perf_counter() - prepare_started) * 1000

        setup_started = time.perf_counter()
        service = tool._get_service()
        service._submit(lambda: None)
        viewport_evidence = _prepare_browser_viewport(service)
        origins = {
            _origin(benchmark.config.get_environment(site).active_url) for site in SITE_PORTS
        }
        _clear_browser_state(service, origins)
        readiness_retries = _login_context(service, sites, benchmark, start_urls, viewport_evidence)
        _verify_session_viewport(service, viewport_evidence, "after_login")
        service._submit(lambda: recorder.start(service._context))
        timings["browser_session_setup_ms"] = (time.perf_counter() - setup_started) * 1000

        before = resources.counters()
        resource_started = time.perf_counter()
        sampler.start()
        sampler_started = True
        replay_started = time.perf_counter()
        for index, arguments in enumerate(calls, start=1):
            call_started = time.perf_counter()
            diagnostics: dict[str, Any] = {}
            result_status, result_text = _execute_replay_call(
                tool, arguments, diagnostics=diagnostics
            )
            dispatched_before_timeout = _click_was_dispatched_before_navigation_timeout(
                arguments, result_status, result_text
            )
            effective_status, observation_error = _replay_step_status(
                arguments,
                result_status,
                result_text,
                dispatched=dispatched_before_timeout,
            )
            record = {
                "index": index,
                "arguments": arguments,
                "status": effective_status,
                "tool_status": result_status,
                "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                "result": result_text,
            }
            if diagnostics:
                record["action_diagnostics"] = diagnostics
            if observation_error is not None:
                record["observation_status"] = "error"
                record["observation_error"] = observation_error
            if dispatched_before_timeout:
                record["recovery"] = {
                    "classification": "click_dispatched_navigation_wait_timeout",
                    "action_retried": False,
                }
            records.append(record)
            if effective_status != "success":
                try:
                    record["failure_ui_state"] = _capture_failure_ui_state(service, arguments)
                except Exception as observation_exc:
                    record["failure_ui_state_error"] = (
                        f"{type(observation_exc).__name__}: {observation_exc}"
                    )[:300]
                detail = observation_error or result_text
                raise RuntimeError(f"step {index} failed: {detail}")
        timings["trajectory_replay_ms"] = (time.perf_counter() - replay_started) * 1000
        _verify_session_viewport(service, viewport_evidence, "after_trajectory")
        process_status = "pass"

        if state_contract is not None:
            from harness.webarena_state import capture_service_form

            state_observation = capture_service_form(
                service, state_contract, run_id=run_id, attempt_id=attempt_id, engine=engine
            )
            (attempt_dir / "final_state.json").write_text(
                json.dumps(state_observation, ensure_ascii=False, indent=2), encoding="utf-8"
            )

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
        is_retrieval = official_task.is_retrieve_task
        if is_retrieval:
            retrieval_acceptance = build_actual_response(
                contract=retrieval_contract,
                task_id=task_id,
                calls=candidate["tool_calls"],
                records=records,
                run_id=run_id or output_dir.name,
                attempt_id=attempt_id,
            )
            (attempt_dir / "retrieval_acceptance.json").write_text(
                json.dumps(retrieval_acceptance, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            actual_response = retrieval_acceptance["actual_response"]
        else:
            actual_response = candidate["recorded_response"]
        if actual_response is not None:
            (attempt_dir / "actual_response.json").write_text(
                json.dumps(actual_response, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        evaluation_result = (
            (
                evaluate_corrected_contract(
                    benchmark,
                    corrected_contract,
                    agent_response=actual_response,
                    network_trace=har_path,
                )
                if corrected_contract is not None
                else benchmark.evaluate_task(
                    task_id=task_id,
                    agent_response=actual_response,
                    network_trace=har_path,
                )
            )
            if actual_response is not None and state_contract is None
            else None
        )
        evaluation = (
            evaluation_result.model_dump(mode="json")
            if evaluation_result is not None
            else {
                "task_id": task_id,
                "status": "unsupported",
                "score": None,
                "reason": "Current-run retrieval evidence is not sufficient for evaluation",
                "evaluators_results": [],
            }
        )
        if state_contract is not None:
            from harness.webarena_state import evaluate_task as evaluate_state_task

            evaluation = evaluate_state_task(
                benchmark,
                state_contract,
                agent_response=actual_response,
                network_trace=har_path,
                observation=state_observation,
                run_id=run_id,
                attempt_id=attempt_id,
                engine=engine,
            )
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
            "unsupported"
            if evaluation.get("status") == "unsupported"
            else "pass"
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
        cleanup_errors: list[str] = []
        duration_ms = (time.perf_counter() - started) * 1000
        if sampler_started:
            try:
                sampler.stop()
            except Exception as exc:
                cleanup_errors.append(f"resource sampler stop failed: {exc}")
        try:
            after = resources.counters() if before is not None else None
        except Exception as exc:
            after = None
            cleanup_errors.append(f"final resource counters failed: {exc}")
        resource_duration_ms = (
            (time.perf_counter() - resource_started) * 1000 if resource_started is not None else 0.0
        )
        try:
            tool.close()
        except Exception as exc:
            cleanup_errors.append(f"browser tool close failed: {exc}")

    timings["browser_resource_measurement_ms"] = resource_duration_ms

    trace_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "schema": "lex_webarena_trajectory_replay_attempt/1",
        "run_id": run_id or output_dir.name,
        "attempt_id": attempt_id,
        "retrieval_protocol": (
            retrieval_contract["schema"] if retrieval_contract is not None else PROTOCOL
        ),
        "retrieval_acceptance": retrieval_acceptance,
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
        "evaluation_input_sha256": _json_sha256(actual_response)
        if actual_response is not None
        else None,
        "source_model": (candidate.get("method") or {}).get("model"),
        "source_engine": (candidate.get("method") or {}).get("engine"),
        "source_kind": (candidate.get("source") or {}).get("kind", "browser_use_execution"),
        "action_count": len(calls),
        "completed_action_count": sum(record["status"] == "success" for record in records),
        "process_status": process_status,
        "evaluator_status": evaluator_status,
        "replay_assertions": list(replay_assertions),
        **(
            {"state_contract_sha256": state_contract.definition_sha256}
            if state_contract is not None
            else {}
        ),
        "contract_correction_id": (
            corrected_contract.correction_id if corrected_contract is not None else None
        ),
        "contract_correction_source_sha256": (
            corrected_contract.correction_source_sha256 if corrected_contract is not None else None
        ),
        "corrected_task_sha256": (
            corrected_contract.corrected_task_sha256 if corrected_contract is not None else None
        ),
        "status": "unsupported"
        if evaluator_status == "unsupported"
        else "pass"
        if process_status == evaluator_status == "pass"
        else "fail",
        "duration_ms": round(duration_ms, 3),
        "timings": {key: round(value, 3) for key, value in timings.items()},
        "browser_session_readiness_retries": readiness_retries,
        "viewport": viewport_evidence,
        "resource": (
            _resource_summary(resources, sampler, before, after, resource_duration_ms)
            if before is not None and after is not None and sampler.samples
            else {}
        ),
        "error": error,
        "cleanup_errors": cleanup_errors,
        "evidence": {
            "trace": _evidence_file(trace_path),
            **(
                {"final_state": _evidence_file(attempt_dir / "final_state.json")}
                if state_contract is not None
                else {}
            ),
            "retrieval_acceptance": _evidence_file(attempt_dir / "retrieval_acceptance.json"),
            "actual_response": _evidence_file(attempt_dir / "actual_response.json"),
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


def _attempt_needs_retry(attempt: dict[str, Any]) -> bool:
    """Give every failed logical replay exactly one clean physical retry."""
    return attempt.get("status") not in {"pass", "unsupported"}


def _embedded_observation_error(
    arguments: dict[str, Any], result_status: str, result_text: str
) -> str | None:
    """Expose BrowserTool snapshot degradation as a replay failure.

    BrowserTool currently returns a successful tool status with a bracketed error
    payload when the page execution context changes during a snapshot.  The action
    remains completed, but the required observation is unavailable.  Both replay
    runners use this classification to fail the step and retain the exact error.
    """

    if arguments.get("action") != "snapshot" or result_status != "success":
        return None
    prefix = "[Snapshot error:"
    if not result_text.startswith(prefix):
        return None
    return result_text[len(prefix) :].removesuffix("]").strip()


def _replay_step_status(
    arguments: dict[str, Any], result_status: str, result_text: str, *, dispatched: bool
) -> tuple[str, str | None]:
    """Apply the same snapshot failure policy in every replay runner."""

    observation_error = _embedded_observation_error(arguments, result_status, result_text)
    if observation_error is not None:
        return "error", observation_error
    return ("success" if dispatched else result_status), None


def _diagnostic_locator(selector: str) -> str:
    """Expand visibility/ordinal filters without confusing CSS with locators.

    Diagnostic candidates intentionally include hidden/zero-area elements.
    The original action and its locator remain unchanged.
    """
    output = []
    quote = None
    index = 0
    while index < len(selector):
        char = selector[index]
        if char == "\\" and index + 1 < len(selector):
            output.append(selector[index:index + 2])
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif selector.startswith(":visible", index) and (
            index + 8 == len(selector)
            or not (selector[index + 8].isalnum() or selector[index + 8] in "_-\\")
        ):
            index += 8
            continue
        output.append(char)
        index += 1
    diagnostic = "".join(output)
    # :nth-match(...:visible, 2) can fail because an earlier sibling is
    # zero-area. Keeping the ordinal would hide that missing first match.
    if diagnostic.startswith(":nth-match(") and diagnostic.endswith(")"):
        inner = diagnostic[len(":nth-match("):-1]
        candidate, separator, ordinal = inner.rpartition(",")
        if separator and ordinal.strip().isdigit():
            diagnostic = candidate.strip()
    return diagnostic


def _capture_failure_ui_state(service: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Retain bounded DOM geometry after a failed action without form values.

    A screenshot cannot distinguish a visible text fallback from a hidden
    select, and a tool timeout does not preserve the locator's final state.
    This observation is diagnostic only; it never changes the tool/evaluator
    verdict or retries the action.
    """

    selector = arguments.get("selector")
    if not isinstance(selector, str) or len(selector) > 1000:
        selector = None
    def observe() -> dict[str, Any]:
        result = service._page.evaluate(
            """selector => {
              const state = element => {
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return {tag: element.tagName.toLowerCase(), id: element.id || null,
                  name: element.getAttribute('name'), hidden: element.hidden,
                  disabled: !!element.disabled, display: style.display,
                  visibility: style.visibility,
                  rect: [rect.x, rect.y, rect.width, rect.height]};
              };
              const result = {page_origin_path: location.origin + location.pathname,
                ready_state: document.readyState, selector, native_selector: null,
                selector_count: null,
                selector_error: null, matched_controls: [], region_controls: []};
              result.region_controls = [...document.querySelectorAll(
                'select[name*="region"],input[name*="region"],'
                + 'select[id*="region"],input[id*="region"]')]
                .slice(0, 16).map(state);
              return result;
            }""",
            selector,
        )
        if selector:
            diagnostic = _diagnostic_locator(selector)
            result["diagnostic_selector"] = diagnostic
            result["selector_engine"] = "playwright"
            result["candidate_scope"] = "visibility_and_outer_ordinal_filters_removed"
            try:
                observed = service._page.locator(diagnostic).evaluate_all(
                    """elements => ({selector_count: elements.length,
                      matched_controls: elements.slice(0,8).map(element => {
                        const r=element.getBoundingClientRect(),s=getComputedStyle(element);
                        return {tag:element.tagName.toLowerCase(),id:element.id||null,
                          name:element.getAttribute('name'),hidden:element.hidden,
                          disabled:!!element.disabled,display:s.display,visibility:s.visibility,
                          rect:[r.x,r.y,r.width,r.height],client_rect_count:element.getClientRects().length,
                          has_offset_parent:element.offsetParent!==null};
                      })})"""
                )
                result.update(observed)
            except Exception as exc:
                result["selector_error"] = type(exc).__name__
        return result

    return service._submit(observe)


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


def _finalize_method_teardown(
    client: Any,
    run_id: str,
    engine: str,
    method_teardown: dict[str, bool],
    receipt: dict[str, Any],
    receipt_path: Path,
    primary_error: BaseException | None,
) -> None:
    """Persist cleanup outcome without replacing an in-flight task failure."""

    cleanup_errors = []
    try:
        _teardown_method(client, run_id, engine)
    except Exception as exc:
        cleanup_errors.append(str(exc))
        method_teardown[engine] = False
        receipt.setdefault("method_teardown_errors", {})[engine] = str(exc)
    else:
        method_teardown[engine] = True
        (receipt.get("method_teardown_errors") or {}).pop(engine, None)
    receipt["method_teardown"] = method_teardown
    try:
        _write_receipt(receipt_path, receipt)
    except Exception as exc:
        cleanup_errors.append(f"receipt update failed: {exc}")
    if not cleanup_errors:
        return
    message = "method finalization failed: " + "; ".join(cleanup_errors)
    if primary_error is not None:
        primary_error.add_note(message)
        return
    raise RuntimeError(message)


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


def _reject_partial_method_resume(
    keys: list[tuple[str, int]], engines: list[str], task_ids: list[int]
) -> None:
    for engine in engines:
        completed_for_method = sum(key[0] == engine for key in keys)
        if 0 < completed_for_method < len(task_ids):
            raise ValueError(
                "a method-scoped site cohort cannot resume after a partial method; "
                "restart that method from a fresh site preparation in a new output directory"
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

    def required_receipt_int(item: object, field: str, record: str) -> int:
        if not isinstance(item, dict) or field not in item:
            raise ValueError(f"existing replay receipt {record} omitted {field}")
        value = item[field]
        if isinstance(value, bool):
            raise ValueError(f"existing replay receipt {record} has invalid {field}")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"existing replay receipt {record} has invalid {field}") from exc

    from harness.webarena_state import validate_attempt_identities

    validate_attempt_identities(attempts, set(plan.get("state_contracts", {})))
    keys = []
    for item in attempts:
        task_id = required_receipt_int(item, "task_id", "attempt")
        if task_id in plan.get("state_contracts", {}):
            from harness.webarena_state import verify_attempt as verify_state_attempt

            if item.get("status") == "pass" and not (
                item.get("process_status") == item.get("evaluator_status") == "pass"
            ):
                raise ValueError("claimed state pass contradicts process or evaluator status")
            if item.get("run_id") != receipt.get("run_id"):
                raise ValueError("cross-run form-state attempt cannot resume")
            if item.get("process_status") == "pass" and item.get("status") != "unsupported":
                verify_state_attempt(item, plan["state_contracts"][task_id])
        if task_id in plan.get("retrieval_task_ids", []):
            from .retrieval_acceptance import verify_attempt

            if item.get("status") == "pass" and not (
                item.get("process_status") == item.get("evaluator_status") == "pass"
            ):
                raise ValueError("claimed retrieval pass contradicts process or evaluator status")
            contract = plan.get("retrieval_contracts", {}).get(task_id)
            if contract is None:
                raise ValueError("resumed retrieval requires a frozen observation contract")
            if item.get("retrieval_protocol") != contract["schema"] or item.get(
                "run_id"
            ) != receipt.get("run_id"):
                raise ValueError("legacy or cross-run retrieval attempt cannot resume a new cohort")
            if item.get("process_status") == "pass" and item.get("status") != "unsupported":
                source = next(
                    task
                    for task in plan["seed_manifest"]["tasks"]
                    if int(task["task_id"]) == task_id
                )
                verify_attempt(
                    item,
                    contract,
                    _candidate(source)["tool_calls"],
                    expected_data_checksum=str(
                        plan["seed_manifest"].get("source", {}).get("official_data_checksum") or ""
                    ),
                )
        if item.get("schema") != "lex_webarena_trajectory_replay_attempt/1":
            raise ValueError("existing replay receipt contains an invalid attempt schema")
        key = (str(item.get("engine")), task_id)
        if key not in allowed_keys:
            raise ValueError("existing replay receipt contains an attempt outside the frozen plan")
        if item.get("source_candidate_id") != canonical_ids[key[1]]:
            raise ValueError("existing replay receipt contains a non-canonical trajectory")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ValueError("existing replay receipt contains duplicate method/task attempts")
    if plan["profile"]["execution"]["reset_before_every_task"] is False:
        _reject_partial_method_resume(keys, engines, task_ids)
        site_preparation = receipt.get("method_site_preparation") or {}
        for engine in engines:
            if any(key[0] == engine for key in keys) and not (
                isinstance(site_preparation.get(engine), dict)
                and site_preparation[engine].get("status") == "complete"
            ):
                raise ValueError("completed method lacks a site preparation receipt")
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
        (
            str(item.get("engine")),
            required_receipt_int(item, "task_id", "physical execution"),
            required_receipt_int(item, "physical_execution", "physical execution"),
        )
        for item in physical_executions
    ]
    if len(physical_keys) != len(set(physical_keys)):
        raise ValueError("existing replay receipt contains duplicate physical executions")
    physical_by_logical: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for item in physical_executions:
        key = (
            str(item.get("engine")),
            required_receipt_int(item, "task_id", "physical execution"),
        )
        if key not in allowed_keys:
            raise ValueError(
                "existing replay receipt contains a physical execution outside the plan"
            )
        physical_by_logical.setdefault(key, []).append(item)
    attempts_by_key = {(str(item["engine"]), int(item["task_id"])): item for item in attempts}
    if set(physical_by_logical) != set(attempts_by_key):
        raise ValueError("existing replay receipt has an unbound physical execution")
    for key, attempt in attempts_by_key.items():
        physical = physical_by_logical[key]
        if len(physical) != int(attempt.get("physical_execution_count") or 1) or max(
            int(item["physical_execution"]) for item in physical
        ) != int(attempt["physical_execution"]):
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
    qualification_task_ids: list[int] | None = None,
    correction_task_ids: list[int] | None = None,
    qualified_dataset_path: str | Path | None = None,
) -> dict[str, Any]:
    plan = load_replay_plan(profile_path, seed_manifest_path)
    seeds = plan["seed_manifest"]
    selected_scopes = sum(
        scope is not None
        for scope in (diagnostic_task_ids, qualification_task_ids, correction_task_ids)
    )
    if selected_scopes > 1:
        raise ValueError("diagnostic, qualification and correction task scopes are exclusive")
    selected_task_ids = (
        qualification_task_ids
        if qualification_task_ids is not None
        else correction_task_ids
        if correction_task_ids is not None
        else diagnostic_task_ids
    )
    task_ids = list(plan["task_ids"] if selected_task_ids is None else selected_task_ids)
    tasks = select_tasks(seeds, task_ids)
    engines = list(plan["engines"])
    if qualification_engine is not None:
        if (
            diagnostic_task_ids is not None
            or diagnostic_engines is not None
            or correction_task_ids is not None
        ):
            raise ValueError("qualification and diagnostic scopes cannot be combined")
        if qualification_engine != plan["qualification_engine"]:
            raise ValueError("qualification engine differs from the frozen reference engine")
        engines = [qualification_engine]
        scope = "qualification_shard" if qualification_task_ids is not None else "qualification"
    elif qualification_task_ids is not None:
        raise ValueError("qualification-task requires qualification-engine")
    elif correction_task_ids is not None:
        if diagnostic_engines is not None:
            raise ValueError("formal correction shards must cover every registered browser")
        scope = "formal_correction_shard"
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
    if scope != "diagnostic":
        from .retrieval_acceptance import require_current_retrieval

        require_current_retrieval(plan)
    if scope in {"formal", "formal_correction_shard"}:
        if qualified_dataset_path is None:
            raise ValueError("formal replay requires a complete qualified trajectory dataset")
        qualification_dataset_sha256 = validate_qualification_dataset(plan, qualified_dataset_path)
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
    # Formal evidence must be reproducible from a clean checkout.  A diagnostic
    # is explicitly non-promotable and may be run while investigating an
    # uncommitted candidate fix; retain the exact base commit instead of
    # rejecting that workflow as if it were formal evidence.
    browser_eval_commit = (
        _require_clean_git_checkout(repo_root)
        if scope != "diagnostic"
        else subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    cowagent_commit = _require_clean_git_checkout(cowagent)
    client = docker.from_env()
    docker_info = client.info()
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
        "browser_session_contract": _browser_session_contract(),
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
    method_site_preparation = {}
    if receipt_path.is_file():
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        method_site_preparation = dict(previous.get("method_site_preparation") or {})
    lock_path = _physical_execution_lock_path(_docker_daemon_lock_namespace(docker_info))
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
        "method_site_preparation": method_site_preparation,
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
            execution = plan["profile"]["execution"]
            reset_before_every_task = execution["reset_before_every_task"]
            retry_failed_task = execution.get("retry_failed_task", True)
            pending = [task for task in tasks if (engine, int(task["task_id"])) not in completed]
            if not pending:
                continue
            network = None
            container = None
            primary_error: BaseException | None = None
            try:
                site_preparation_started = time.perf_counter()
                site_preparation_started_at = round(time.time() * 1000)
                _stop_all_sites()
                if not reset_before_every_task:
                    _prepare_sites(tuple(SITE_PORTS))
                    method_site_preparation[engine] = {
                        "status": "complete",
                        "started_at_unix_ms": site_preparation_started_at,
                        "completed_at_unix_ms": round(time.time() * 1000),
                        "duration_ms": round(
                            (time.perf_counter() - site_preparation_started) * 1000, 3
                        ),
                        "sites": [site.value for site in SITE_PORTS],
                    }
                    receipt["method_site_preparation"] = method_site_preparation
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
                        state_contract=plan.get("state_contracts", {}).get(int(task["task_id"])),
                        retrieval_contract=plan.get("retrieval_contracts", {}).get(
                            int(task["task_id"])
                        ),
                        run_id=run_id,
                        prepare_sites=reset_before_every_task,
                    )
                    attempt["browser_session_id"] = session["id"]
                    _finish_physical_execution(physical_entry, attempt)
                    _write_receipt(receipt_path, receipt)
                    if retry_failed_task and _attempt_needs_retry(attempt):
                        # A declared replay gets one clean retry regardless of where the first
                        # physical execution failed. Restarting the browser and rerunning
                        # _run_one also repeats site preparation, so failed page/session state
                        # cannot leak into the final physical execution.
                        container.restart()
                        container.reload()
                        http_endpoint = _published_endpoint(
                            container, engine=engine, cdp_port=ENGINE_PORTS[engine]
                        )
                        endpoint = _wait_cdp(http_endpoint, container=container)
                        prior_attempt = attempt
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
                            state_contract=plan.get("state_contracts", {}).get(
                                int(task["task_id"])
                            ),
                            retrieval_contract=plan.get("retrieval_contracts", {}).get(
                                int(task["task_id"])
                            ),
                            run_id=run_id,
                        )
                        attempt["browser_session_id"] = session["id"]
                        attempt["prior_attempts"] = [prior_attempt]
                        attempt["physical_execution_count"] = 2
                        attempt["duration_ms"] = round(
                            float(prior_attempt["duration_ms"]) + float(attempt["duration_ms"]),
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
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                _finalize_method_teardown(
                    client,
                    run_id,
                    engine,
                    method_teardown,
                    receipt,
                    receipt_path,
                    primary_error,
                )

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
