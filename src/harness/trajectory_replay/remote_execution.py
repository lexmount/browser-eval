"""Shared browser launch, task execution and evidence verification for remote replay."""

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
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from webarena_verified.types.task import WebArenaSite

from harness.distribution import repository_root, source_path
from harness.environments.webarena.har import CdpHarRecorder
from harness.environments.webarena.runtime import (
    browser_tool_config,
    login_context,
    prepare_browser_viewport,
)
from harness.trajectory_replay.catalog_contracts import (
    apply_action_overrides,
    catalog_action_overrides,
)
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.public_webarena.response_builder import build_agent_response
from harness.trajectory_replay.public_webarena.suite import BrowserTask
from harness.trajectory_replay.remote_runtime import (
    COHORT_RESET_SCOPE,
    LEGACY_LOCAL_SOURCE_HOSTS,
    checked_file_sha256,
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
    selected_task_ids,
    validate_method_identity,
    validate_remote_cohort_reset_receipt,
    validate_remote_reset_receipt,
)
from harness.trajectory_replay.retrieval_acceptance import build_actual_response, validate_contract
from harness.trajectory_replay.webarena_corrections import (
    apply_correction,
    check_replay_assertions,
    evaluate_corrected_contract,
    load_corrections,
)
from harness.trajectory_replay.webarena_plan import (
    COMPLETION_CANDIDATES,
    NAVIGATION_CONTEXT_LOSS,
    compile_mutation_observer,
    compile_mutation_readiness,
    mutation_observer_retry_policy,
    replay_site_hosts,
    verify_compiled_mutation_proposal,
)
from harness.trajectory_replay.webarena_replay import (
    _candidate,
    _capture_failure_ui_state,
    _clear_browser_state,
    _click_was_dispatched_before_navigation_timeout,
    _execute_replay_call,
    _json_sha256,
    _replay_step_status,
    select_tasks,
)

NETWORK_SETTLE_MS = 2_000


def sanitize_remote_text(
    message: str, endpoint: str, headers: dict[str, str] | None
) -> str:
    """Remove hosted-browser account and credentials before evidence is persisted."""

    sanitized = message.replace(endpoint, "<remote-cdp-endpoint>")
    sanitized = sanitized.replace(endpoint.split("?", 1)[0], "<remote-cdp-endpoint>")
    for value in (headers or {}).values():
        sanitized = sanitized.replace(value, "<remote-cdp-credential>")
        if " " in value:
            sanitized = sanitized.replace(
                value.split(" ", 1)[1], "<remote-cdp-credential>"
            )
    return sanitized


def _sanitize_remote_payload(value: object, endpoint: str, headers: dict[str, str]) -> object:
    if isinstance(value, str):
        return sanitize_remote_text(value, endpoint, headers)
    if isinstance(value, list):
        return [_sanitize_remote_payload(item, endpoint, headers) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize_remote_payload(item, endpoint, headers)
            for key, item in value.items()
        }
    return value


def finalize_timed_out_task(
    task_dir: Path,
    row: dict,
    *,
    duration_ms: int,
    task_timeout_ms: int,
    runtime_metadata: dict[str, str] | None = None,
    remote_endpoint: str | None = None,
    remote_headers: dict[str, str] | None = None,
) -> dict:
    """Commit a browser runtime failure after an external process watchdog fires."""

    partial_path = task_dir / ".trace.partial.json"
    trace_path = task_dir / "trace.json"
    records_path = partial_path if partial_path.is_file() else trace_path
    records = (
        json.loads(records_path.read_text(encoding="utf-8"))
        if records_path.is_file()
        else []
    )
    if records and records[-1].get("status") == "running":
        active = records[-1]
        active["status"] = "fail"
        active["duration_ms"] = duration_ms
        active["error"] = (
            f"ExternalTaskTimeout: browser stopped responding during action "
            f"{active.get('index')} ({active.get('action')}) and exceeded the "
            f"{task_timeout_ms} ms task budget"
        )
    else:
        records.append(
            {
                "index": None,
                "action": "task_timeout",
                "arguments": {},
                "status": "fail",
                "duration_ms": duration_ms,
                "output": "",
                "error": (
                    "ExternalTaskTimeout: browser stopped responding before the active "
                    f"action boundary was persisted and exceeded the {task_timeout_ms} ms "
                    "task budget"
                ),
            }
        )
    if remote_endpoint is not None and remote_headers is not None:
        records = _sanitize_remote_payload(  # type: ignore[assignment]
            records, remote_endpoint, remote_headers
        )
    message = records[-1]["error"]
    hashes = {
        "trace": _write(trace_path, records),
        "readiness": _write(task_dir / "readiness.json", []),
        "outputs": _write(task_dir / "outputs.json", {}),
    }
    partial_path.unlink(missing_ok=True)
    result = {
        "task_id": row["task_id"],
        "status": "fail",
        "score": None,
        "action_count": len(row["actions"]),
        "executed_actions_sha256": _json_sha256(row["actions"]),
        "omitted_diagnostic_observer_sha256": row.get(
            "omitted_diagnostic_observer_sha256"
        ),
        "completed_actions": sum(
            record.get("status") in {"pass", "success"} for record in records
        ),
        "duration_ms": duration_ms,
        "error": message,
        "failure_stage": "browser_tool_execution",
        "runtime_poisoned": True,
        **(
            {"runtime_metadata": runtime_metadata}
            if runtime_metadata is not None
            else {}
        ),
        "evidence_sha256": hashes,
    }
    result_path = task_dir / "result.json"
    temporary = task_dir / ".result.json.tmp"
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.chmod(0o600)
    temporary.replace(result_path)
    return result


def _effective_retrieval_calls(task_id: int, row: dict, calls: list[dict]) -> list[dict]:
    """Apply the catalog's frozen amendments without changing contract source calls."""

    provenance = row.get("action_overrides")
    catalog = {
        "action_overrides_sha256": (
            provenance.get("source_sha256") if isinstance(provenance, dict) else None
        )
    }
    effective = apply_action_overrides(
        calls,
        catalog_action_overrides(catalog, row),
        task_id=task_id,
    )
    contract = row.get('retrieval_contract')
    if contract is not None:
        validate_contract(contract, calls)
        for observation in contract['sources']:
            index = observation['step'] - 1
            if (index >= len(effective)
                    or _json_sha256(effective[index]) != observation['action_sha256']):
                raise ValueError(f'task {task_id}: qualified observation action changed')
    return effective



def _finalize_network_recorder(
    service, recorder, har_path: Path, *, settle_ms: int = NETWORK_SETTLE_MS
) -> None:
    """Let dispatched background requests finish before freezing their statuses."""

    service._submit(lambda: service._page.wait_for_timeout(settle_ms))
    service._submit(lambda: recorder.stop(service._context, har_path))


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return file_sha256(path)


def _verify_result_evidence(output: Path, rows: list[dict]) -> None:
    for row in rows:
        directory = output / f"task-{row['task_id']}"
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"task {row['task_id']} evidence directory is missing")
        for name, expected in row["evidence_sha256"].items():
            filename = {
                "network_har": "network.har",
                "acceptance": "acceptance.json",
                "actual_response": "actual_response.json",
                "evaluation": "evaluation.json",
                "failure_screenshot": "failure.png",
                "trace": "trace.json",
                "readiness": "readiness.json",
                "site_reset": "site-reset.json",
                "outputs": "outputs.json",
            }.get(name)
            if filename is None:
                raise ValueError(f"task {row['task_id']} has unknown evidence kind {name}")
            path = directory / filename
            if path.is_symlink() or not path.is_file() or file_sha256(path) != expected:
                raise ValueError(f"task {row['task_id']} evidence hash changed: {filename}")


def _wait_cdp(endpoint: str, process: subprocess.Popen) -> None:
    for _ in range(300):
        if process.poll() is not None:
            raise RuntimeError(f"browser exited before CDP was ready: {process.returncode}")
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.1)
    raise TimeoutError("browser CDP did not become ready")


def _browser_command(args: argparse.Namespace, port: int, profile: str) -> list[str]:
    if args.engine == "chromium":
        command = [
            str(args.binary),
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            # Respect the explicitly configured HTTP fixture origins. An
            # automatic scheme upgrade changes exact evaluator URL identity.
            "--disable-features=HttpsUpgrades,HttpsFirstBalancedMode,HttpsFirstModeIncognito",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "about:blank",
        ]
        host_resolver_rules = getattr(args, "host_resolver_rules", None)
        if host_resolver_rules:
            if not isinstance(host_resolver_rules, str) or "\n" in host_resolver_rules:
                raise ValueError("Chromium host resolver rules must be one line")
            command.insert(-1, f"--host-resolver-rules={host_resolver_rules}")
        return command
    if args.engine == "moli":
        return [
            str(args.binary),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--layout",
            "--font",
            "--profile-dir",
            profile,
        ]
    if args.engine == "lightpanda":
        return [
            str(args.binary),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
    if args.engine == "obscura":
        return [
            str(args.binary),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--storage-dir",
            profile,
            "--allow-private-network",
        ]
    raise ValueError(f"unknown local replay engine: {args.engine}")


def _task_type(task: object) -> str:
    expected = [
        item.expected.model_dump(mode="json").get("task_type")
        for item in task.eval
        if item.evaluator == "AgentResponseEvaluator"
    ]
    if len(expected) != 1 or expected[0] not in {"NAVIGATE", "MUTATE"}:
        raise ValueError(f"task {task.task_id}: current-run response type unavailable")
    return expected[0]


def _read_retrieval_contract(
    task_id: int, row: dict, sites: dict, candidate_path: Path | None = None
) -> dict:
    embedded_contract = row.get("retrieval_contract")
    embedded_calls = row.get("source_actions")
    if embedded_contract is not None or embedded_calls is not None:
        if not isinstance(embedded_contract, dict) or not isinstance(embedded_calls, list):
            raise ValueError(f"task {task_id}: embedded retrieval input is incomplete")
        candidate = {
            "task_id": task_id,
            "tool_calls": embedded_calls,
            "retrieval_contract": embedded_contract,
        }
        if embedded_contract.get("task_id") != task_id:
            raise ValueError(f"task {task_id}: retrieval contract identity mismatch")
        source_sites = {
            WebArenaSite(site): url for site, url in (row.get("source_site_urls") or {}).items()
        }
        rebound = rebind_remote_calls(
            _effective_retrieval_calls(task_id, row, embedded_calls),
            sites,
            {
                "onestopmarket.example.com",
                "gitlab.example.com",
                "openstreetmap.example.com",
                "wikipedia.example.com",
                "ec2-3-133-227-75.us-east-2.compute.amazonaws.com",
                "ec2-3-135-39-80.us-east-2.compute.amazonaws.com",
                "host.docker.internal",
                "127.0.0.1",
                "192.168.5.2",
            },
            source_sites,
        )
        if _json_sha256(rebound) != row["bound_calls_sha256"]:
            raise ValueError(f"task {task_id}: retrieval actions differ from frozen bound calls")
        return candidate
    source_revision = row.get("source_revision") or {}
    path = candidate_path
    if path is None and source_revision:
        path = Path(source_revision["path"])
    if path is None:
        path = (
            Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")
            / f"task-{task_id}.json"
        )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    if candidate["task_id"] != task_id or candidate["retrieval_contract"]["task_id"] != task_id:
        raise ValueError(f"task {task_id}: retrieval contract identity mismatch")
    rebound = rebind_remote_calls(
        _effective_retrieval_calls(task_id, row, candidate["tool_calls"]),
        sites,
        set(LEGACY_LOCAL_SOURCE_HOSTS),
    )
    if _json_sha256(rebound) != row["bound_calls_sha256"]:
        raise ValueError(f"task {task_id}: retrieval actions differ from frozen bound calls")
    return candidate


def _evaluate_assertions(service: object, contract: object) -> list[dict]:
    selectors = {
        selector
        for assertion in contract.replay_assertions
        for selector in (assertion.get("fields") or {})
    }
    state = service._submit(
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
    return list(
        check_replay_assertions(
            contract, page_url=state["url"], page_text=state["text"], field_values=state["fields"]
        )
    )


def _run_task(
    *,
    task_id: int,
    row: dict,
    benchmark: object,
    endpoint: str,
    output: Path,
    run_id: str,
    origins: set[str],
    sites: dict,
    catalog: object,
    candidate_override_path: Path | None = None,
    portable_task: BrowserTask | None = None,
    portable_action_timeout_ms: int = 30_000,
    backend: str = "browsertool",
    engine: str = "chromium",
    cdp_headers: dict[str, str] | None = None,
    expected_runtime_metadata: dict[str, str] | None = None,
    request_bridge_origins: set[str] | None = None,
    request_bridge_host_overrides: list[dict[str, str]] | None = None,
    task_timeout_ms: int = 300_000,
) -> dict:
    if task_timeout_ms < 1:
        raise ValueError("task_timeout_ms must be positive")
    official = benchmark.get_task(task_id)
    correction = apply_correction(official, catalog) if task_id in catalog.records else None
    if backend == "browsertool":
        if cdp_headers:
            raise ValueError("BrowserTool replay does not support remote CDP headers")
        from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

        tool = BrowserTool(config=browser_tool_config(endpoint))
    elif backend == "playwright":
        from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

        tool = PlaywrightReplayTool(
            endpoint,
            engine=engine,
            headers=cdp_headers,
            request_bridge_origins=request_bridge_origins,
            request_bridge_host_overrides=request_bridge_host_overrides,
        )
    else:
        raise ValueError(f"unknown replay backend: {backend}")
    if (
        expected_runtime_metadata is not None
        and getattr(tool, "runtime_metadata", None) != expected_runtime_metadata
    ):
        observed = getattr(tool, "runtime_metadata", None)
        tool.close()
        raise RuntimeError(
            "hosted browser version changed before task execution: "
            f"expected {expected_runtime_metadata!r}, observed {observed!r}"
        )
    recorder = CdpHarRecorder(
        capture_transport=getattr(tool, "har_capture_transport", "cdp"),
        capture_new_pages=getattr(tool, "_supports_multiple_pages", True),
        playwright_request_transform=getattr(tool, "har_request_transform", None),
    )
    task_dir = output / f"task-{task_id}"
    task_dir.mkdir(mode=0o700)
    partial_trace_path = task_dir / ".trace.partial.json"
    har_path = task_dir / "network.har"
    records: list[dict] = []
    portable_outputs: dict[str, str] = {}
    readiness_records: list[dict] = []
    hashes: dict[str, str] = {}
    started = time.perf_counter()
    deadline = started + task_timeout_ms / 1_000

    def remaining_task_ms() -> int:
        remaining_ms = int((deadline - time.perf_counter()) * 1_000)
        if remaining_ms < 1:
            raise TimeoutError(f"task exceeded its {task_timeout_ms} ms execution budget")
        return remaining_ms

    def bounded_call(call: dict) -> dict:
        remaining_ms = remaining_task_ms()
        declared = int(call.get("timeout_ms", portable_action_timeout_ms))
        return {**call, "timeout_ms": min(declared, remaining_ms)}

    status = "fail"
    score = None
    error = ""
    failure_stage = "browser_session_setup"
    service = None
    recorder_started = False
    try:
        service = tool._get_service()
        service._submit(lambda: None)
        viewport = prepare_browser_viewport(service)
        _clear_browser_state(service, origins)
        start_urls = list(
            benchmark.config.render_url(list(official.start_urls), list(official.sites))
        )
        login_context(service, tuple(official.sites), benchmark, start_urls, viewport)
        service._submit(lambda: recorder.start(service._context, service._page))
        recorder_started = True
        failure_stage = "browser_tool_execution"
        if portable_task is not None:
            def persist_partial_trace(current_records: list[dict]) -> None:
                temporary = partial_trace_path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(current_records, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.chmod(0o600)
                temporary.replace(partial_trace_path)

            service._submit(
                lambda: execute_steps(
                    service._page,
                    row["actions"],
                    trace=records,
                    outputs=portable_outputs,
                    task_timeout_ms=remaining_task_ms(),
                    default_action_timeout_ms=portable_action_timeout_ms,
                    layout_observer=getattr(service, "layout_observer", None),
                    navigation_response_observer=recorder.record_navigation_response,
                    document_observer=getattr(service, "document_observer", None),
                    trace_observer=persist_partial_trace,
                )
            )
        for index, action in enumerate(() if portable_task is not None else row["actions"], 1):
            for readiness in row["readiness"]:
                if readiness["before_action"] == index:
                    readiness_input = bounded_call(readiness["input"])
                    ready_status, ready_result = _execute_replay_call(tool, readiness_input)
                    ready_effective_status, ready_observation_error = _replay_step_status(
                        readiness["input"], ready_status, ready_result, dispatched=False
                    )
                    readiness_records.append(
                        {
                            "before_action": index,
                            "status": ready_effective_status,
                            "tool_status": ready_status,
                            "result": ready_result,
                            **(
                                {
                                    "observation_status": "error",
                                    "observation_error": ready_observation_error,
                                }
                                if ready_observation_error is not None
                                else {}
                            ),
                        }
                    )
                    if ready_effective_status != "success":
                        detail = ready_observation_error or ready_result
                        raise RuntimeError(f"readiness before step {index} failed: {detail}")
            call_started = time.perf_counter()
            diagnostics = {}
            result_status, result = _execute_replay_call(
                tool, bounded_call(action), diagnostics=diagnostics
            )
            dispatched = _click_was_dispatched_before_navigation_timeout(
                action, result_status, result
            )
            effective_status, observation_error = _replay_step_status(
                action, result_status, result, dispatched=dispatched
            )
            records.append(
                {
                    "index": index,
                    "arguments": action,
                    "status": effective_status,
                    "tool_status": result_status,
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "result": result,
                    **({"action_diagnostics": diagnostics} if diagnostics else {}),
                    **(
                        {
                            "observation_status": "error",
                            "observation_error": observation_error,
                        }
                        if observation_error is not None
                        else {}
                    ),
                    **(
                        {"recovery": "click_dispatched_navigation_wait_timeout"}
                        if dispatched
                        else {}
                    ),
                }
            )
            if effective_status != "success":
                if getattr(service, "failure_capture_available", True):
                    try:
                        records[-1]["failure_ui_state"] = _capture_failure_ui_state(
                            service, action
                        )
                    except Exception as observation_exc:
                        records[-1]["failure_ui_state_error"] = (
                            f"{type(observation_exc).__name__}: {observation_exc}"
                        )[:300]
                detail = observation_error or result
                raise RuntimeError(f"step {index} failed: {detail}")
        if correction is not None and correction.replay_assertions:
            assertions = _evaluate_assertions(service, correction)
        else:
            assertions = []
        settle_ms = max(
            [NETWORK_SETTLE_MS]
            + [int(action.get("network_settle_ms", 0)) for action in row["actions"]]
        )
        failure_stage = "evidence_finalization"
        _finalize_network_recorder(service, recorder, har_path, settle_ms=settle_ms)
        recorder_started = False
        hashes["network_har"] = file_sha256(har_path)
        if portable_task is not None:
            actual = build_agent_response(portable_task, portable_outputs)
        elif official.is_retrieve_task:
            candidate = _read_retrieval_contract(task_id, row, sites, candidate_override_path)
            # Contract step indices refer to the source calls; recorded results are
            # from the equivalent remote-bound calls executed in this run.
            source_records = [
                dict(record, arguments=candidate["tool_calls"][i])
                for i, record in enumerate(records)
            ]
            acceptance = build_actual_response(
                contract=candidate["retrieval_contract"],
                task_id=task_id,
                calls=candidate["tool_calls"],
                records=source_records,
                run_id=run_id,
                attempt_id=uuid.uuid4().hex,
            )
            hashes["acceptance"] = _write(task_dir / "acceptance.json", acceptance)
            actual = acceptance["actual_response"]
        else:
            actual = {
                "task_type": _task_type(official),
                "status": "SUCCESS",
                "retrieved_data": None,
                "error_details": None,
            }
        hashes["actual_response"] = _write(task_dir / "actual_response.json", actual)
        if actual is None:
            status = "unsupported"
        else:
            failure_stage = "official_evaluator_execution"
            evaluation_result = (
                evaluate_corrected_contract(
                    benchmark, correction, agent_response=actual, network_trace=har_path
                )
                if correction is not None
                else benchmark.evaluate_task(
                    task_id=task_id, agent_response=actual, network_trace=har_path
                )
            )
            evaluation = evaluation_result.model_dump(mode="json")
            evaluation["replay_assertions"] = assertions
            if correction is not None:
                evaluation["contract_correction_id"] = correction.correction_id
                evaluation["corrected_task_sha256"] = correction.corrected_task_sha256
            hashes["evaluation"] = _write(task_dir / "evaluation.json", evaluation)
            score = evaluation.get("score")
            status = (
                "pass"
                if float(score or 0) == 1
                and all(item.get("status") == "pass" for item in assertions)
                else "fail"
            )
    except Exception:
        error = traceback.format_exc()
        failure_capture_available = bool(
            service is not None and getattr(service, "failure_capture_available", True)
        )
        if failure_capture_available and recorder_started:
            try:
                service._submit(lambda: recorder.stop(service._context, har_path))
                hashes["network_har"] = file_sha256(har_path)
            except Exception:
                pass
        if failure_capture_available:
            try:
                service._submit(
                    lambda: service._page.screenshot(path=str(task_dir / "failure.png"))
                )
                hashes["failure_screenshot"] = file_sha256(task_dir / "failure.png")
            except Exception:
                pass
    finally:
        try:
            tool.close()
        except Exception as exc:
            error += f"\n{backend} close failed: {exc}"
    if cdp_headers:
        error = sanitize_remote_text(error, endpoint, cdp_headers)
        records = _sanitize_remote_payload(records, endpoint, cdp_headers)  # type: ignore[assignment]
        readiness_records = _sanitize_remote_payload(  # type: ignore[assignment]
            readiness_records, endpoint, cdp_headers
        )
        portable_outputs = _sanitize_remote_payload(  # type: ignore[assignment]
            portable_outputs, endpoint, cdp_headers
        )
    hashes["trace"] = _write(task_dir / "trace.json", records)
    partial_trace_path.unlink(missing_ok=True)
    hashes["readiness"] = _write(task_dir / "readiness.json", readiness_records)
    if portable_task is not None:
        hashes["outputs"] = _write(task_dir / "outputs.json", portable_outputs)
    outcome = {
        "task_id": task_id,
        "status": status,
        "score": score,
        "action_count": len(row["actions"]),
        "executed_actions_sha256": _json_sha256(row["actions"]),
        "omitted_diagnostic_observer_sha256": row.get("omitted_diagnostic_observer_sha256"),
        "completed_actions": sum(
            record["status"] == ("pass" if portable_task is not None else "success")
            for record in records
        ),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "error": error,
        **({"failure_stage": failure_stage} if error else {}),
        "runtime_poisoned": bool(
            service is not None and not getattr(service, "failure_capture_available", True)
        ),
        **(
            {"runtime_metadata": tool.runtime_metadata}
            if getattr(tool, "runtime_metadata", None) is not None
            else {}
        ),
        **(
            {"request_bridge_stats": tool.request_bridge_stats}
            if request_bridge_origins
            else {}
        ),
        "evidence_sha256": hashes,
    }
    result_path = task_dir / "result.json"
    result_tmp = task_dir / ".result.json.tmp"
    result_tmp.write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result_tmp.chmod(0o600)
    result_tmp.replace(result_path)
    return outcome


ROOT = repository_root()
TASK_SELECTION = (ROOT / "evaluation/trajectory-replay/webarena-verified/selection.json")


def preflight_cohort(args: argparse.Namespace) -> tuple[dict, dict, object, dict[int, dict]]:
    for label, path in (
        ("manifest", args.manifest),
        ("dataset", args.dataset),
        ("browser binary", args.binary),
        ("site map", args.site_map),
        ("cohort reset hook", args.cohort_reset_hook),
    ):
        checked_file_sha256(path, label)
    if args.output.exists():
        raise ValueError("output exists; evidence is append-only")
    validate_method_identity(args.method_id, args.engine)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "webarena_three_method_inputs/1"
        or manifest.get("method_id") != args.method_id
        or manifest.get("engine") != args.engine
        or manifest.get("browser_binary_sha256") != file_sha256(args.binary)
        or manifest.get("dataset_sha256") != file_sha256(args.dataset)
        or manifest.get("selection_sha256") != file_sha256(args.selection)
        or manifest.get("site_map_sha256") != file_sha256(args.site_map)
        or manifest.get("run_count") != 1
        or manifest.get("retry_count") != 0
        or manifest.get("site_reset_between_tasks") is not False
        or manifest.get("promotable") is not True
    ):
        raise ValueError("frozen method, binary, site map or run policy changed")
    for name in ("browser_tool.py", "browser_service.py"):
        checked_file_sha256(args.cowagent / "agent/tools/browser" / name, f"CowAgent {name}")
    sites = load_remote_sites(args.site_map)
    expected_ids = selected_task_ids(args.selection)
    rows = manifest.get("tasks")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    dataset_rows = {int(row["task_id"]): row for row in dataset.get("trajectories") or []}
    if (
        manifest.get("task_ids") != expected_ids
        or not isinstance(rows, list)
        or [row.get("task_id") for row in rows if isinstance(row, dict)] != expected_ids
        or len(rows) != len(expected_ids)
        or len(dataset_rows) != len(expected_ids)
        or set(dataset_rows) != set(expected_ids)
    ):
        raise ValueError("manifest does not contain the exact ordered selected tasks")
    benchmark = remote_benchmark(sites)
    by_id: dict[int, dict] = {}
    for row in rows:
        task_id = row["task_id"]
        actions = row.get("actions")
        source = row.get("source_actions")
        readiness = row.get("readiness")
        if (
            not isinstance(actions, list)
            or not actions
            or not isinstance(source, list)
            or not source
            or not isinstance(readiness, list)
            or _json_sha256(actions) != row.get("bound_calls_sha256")
            or _json_sha256(source) != row.get("source_calls_sha256")
            or rebind_remote_calls(source, sites, set(LEGACY_LOCAL_SOURCE_HOSTS)) != actions
            or _json_sha256(
                {
                    "task": dataset_rows[task_id]["task"],
                    "evaluation_input": dataset_rows[task_id]["evaluation_input"],
                }
            )
            != row.get("task_contract_sha256")
        ):
            raise ValueError(f"task {task_id}: frozen source/bound actions changed")
        for item in readiness:
            if (
                not isinstance(item, dict)
                or type(item.get("before_action")) is not int
                or not 1 <= item["before_action"] <= len(actions)
                or _json_sha256(item.get("input")) != item.get("bound_input_sha256")
            ):
                raise ValueError(f"task {task_id}: readiness call changed")
        contract = row.get("retrieval_contract")
        is_retrieve = benchmark.get_task(task_id).is_retrieve_task
        if is_retrieve:
            if not isinstance(contract, dict):
                raise ValueError(f"task {task_id}: RETRIEVE lacks current-run observation contract")
            if contract.get("task_id") != task_id:
                raise ValueError(f"task {task_id}: retrieval contract task ID changed")
            validate_contract(contract, source)
        elif contract is not None:
            raise ValueError(f"task {task_id}: non-RETRIEVE has a retrieval contract")
        if "recorded_response" in row or "recorded_response" in manifest:
            raise ValueError("frozen answers must not enter the current-run contract")
        by_id[task_id] = row
    return manifest, sites, benchmark, by_id


def cohort_reference_hashes(args: argparse.Namespace) -> dict[str, str]:
    paths = {
        "manifest": args.manifest,
        "browser_binary": args.binary,
        "site_map": args.site_map,
        "cohort_reset_hook": args.cohort_reset_hook,
        "cowagent_browser_tool": args.cowagent / "agent/tools/browser/browser_tool.py",
        "cowagent_browser_service": args.cowagent / "agent/tools/browser/browser_service.py",
        "task_selection": args.selection,
        "frozen_trajectories": args.dataset,
        "task_corrections": ROOT / (
            "evaluation/trajectory-replay/webarena-verified/task-corrections.json"
        ),
        "runner": Path(__file__).resolve(),
        "command_entry": (ROOT / "scripts/run_remote_three_method_cohort.py"),
        "remote_execution": Path(__file__).resolve(),
        "replay_logic": Path(__file__).with_name("webarena_replay.py"),
        "retrieval_acceptance": Path(__file__).with_name("retrieval_acceptance.py"),
        "correction_logic": Path(__file__).with_name("webarena_corrections.py"),
        "remote_runtime": Path(__file__).with_name("remote_runtime.py"),
    }
    catalog = load_corrections()
    for task_id, record in catalog.records.items():
        reference = record.get("reference_receipt")
        if reference:
            paths[f"reference_receipt_{task_id}"] = source_path(reference["path"], root=ROOT)
    return {name: checked_file_sha256(path, name) for name, path in paths.items()}


def certify_cohort(
    *,
    manifest: dict,
    rows: list[dict],
    before: dict[str, str],
    after: dict[str, str],
    evidence_valid: bool,
) -> bool:
    ids = manifest["task_ids"]
    return (
        bool(ids)
        and len(manifest["tasks"]) == len(ids)
        and [task["task_id"] for task in manifest["tasks"]] == ids
        and len(rows) == len(ids)
        and [row["task_id"] for row in rows] == ids
        and len({row["task_id"] for row in rows}) == len(ids)
        and all(
            row["executed_actions_sha256"] == task["bound_calls_sha256"]
            for row, task in zip(rows, manifest["tasks"], strict=True)
        )
        and before == after
        and evidence_valid
    )


def verify_cohort_evidence(output: Path, rows: list[dict], tasks: dict[int, dict]) -> None:
    _verify_result_evidence(output, rows)
    for row in rows:
        task_id = row["task_id"]
        names = set(row["evidence_sha256"])
        if not {"trace", "readiness"} <= names:
            raise ValueError(f"task {task_id}: action/readiness evidence is incomplete")
        if row.get("error"):
            continue
        required = {"network_har", "actual_response"}
        if "retrieval_contract" in tasks[task_id]:
            required.add("acceptance")
        if row.get("score") is not None:
            required.add("evaluation")
        if not required <= names:
            raise ValueError(f"task {task_id}: evaluator/current-run evidence is incomplete")


def reset_cohort(args: argparse.Namespace, manifest: dict, sites: dict) -> str:
    """Restore the site baseline once for this method's complete ordered cohort."""

    receipt_path = args.output / "site-reset.json"
    started_at = time.time()
    subprocess.run(
        [
            sys.executable,
            str(args.cohort_reset_hook),
            "--method-id",
            args.method_id,
            "--app-id",
            str(args.app_id),
            "--site-map",
            str(args.site_map),
            "--receipt",
            str(receipt_path),
        ],
        check=True,
        timeout=1800,
    )
    admin = urlsplit(sites[WebArenaSite.SHOPPING_ADMIN])
    validated = validate_remote_cohort_reset_receipt(
        receipt_path,
        site_map_sha256=manifest["site_map_sha256"],
        app_id=args.app_id,
        reset_scope=COHORT_RESET_SCOPE,
        site_reset_between_tasks=False,
        admin_https_base_url=(
            f"{admin.scheme}://{admin.netloc}/" if admin.scheme == "https" else None
        ),
        required_readiness_probes=manifest.get("site_readiness"),
    )
    if (
        validated.get("method_id") != args.method_id
        or validated.get("task_id") is not None
        or validated.get("completed_at_unix") is None
        or not started_at - 1800 <= float(validated["completed_at_unix"]) <= time.time()
    ):
        raise ValueError("cohort reset receipt is stale or belongs to another method")
    return file_sha256(receipt_path)


def verify_browser_session(tool: object, sites: dict | None = None) -> list[dict]:
    """Reject shared session setup errors before recording task outcomes."""
    try:
        service = tool._get_service()
        service._submit(lambda: None)
        prepare_browser_viewport(service)

        def check_sites() -> list[dict]:
            rows = []
            for site, url in (sites or {}).items():
                page = service._page
                response = page.goto(url, wait_until="load", timeout=60000)
                expected, actual = urlsplit(url), urlsplit(page.url)
                if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
                    raise ValueError(f"{site.value}: browser changed configured site origin")
                if response is None or response.status != 200:
                    raise ValueError(f"{site.value}: browser readiness did not return HTTP 200")
                if site == WebArenaSite.SHOPPING_ADMIN:
                    scripts = page.locator("script[src]").evaluate_all(
                        "nodes => nodes.map(n => n.src)"
                    )
                    if not scripts or any(
                        urlsplit(src).scheme != expected.scheme for src in scripts
                    ):
                        raise ValueError(
                            "shopping_admin: script protocol differs from configured origin"
                        )
                    if page.evaluate("typeof require") != "function":
                        raise ValueError("shopping_admin: Magento JavaScript loader is unavailable")
                rows.append(
                    {
                        "site": site.value,
                        "requested_url": url,
                        "actual_url": page.url,
                        "status": response.status,
                    }
                )
            return rows

        return service._submit(check_sites)
    finally:
        tool.close()


def main_cohort(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute one complete frozen remote replay method")
    parser.add_argument("--selection", type=Path, default=TASK_SELECTION)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--method-id", required=True)
    parser.add_argument("--engine", choices=("moli", "chromium"), required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--cowagent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app-id", type=int, required=True)
    parser.add_argument("--cohort-reset-hook", type=Path, required=True)
    parser.add_argument(
        "--tasks",
        help="Explicit correction subset, in numeric order; never a publishable complete cohort",
    )
    args = parser.parse_args(argv)
    return run_cohort(args)


def run_cohort(args: argparse.Namespace) -> int:
    previous_mask = os.umask(0o077)
    previous_path = list(sys.path)
    try:
        return _execute_cohort(args)
    finally:
        sys.path[:] = previous_path
        os.umask(previous_mask)


def _execute_cohort(args: argparse.Namespace) -> int:
    frozen_inputs = cohort_reference_hashes(args)
    manifest, sites, benchmark, tasks = preflight_cohort(args)
    execution_manifest = manifest
    if args.tasks:
        selected = [int(value) for value in args.tasks.split(",")]
        if not selected or selected != sorted(set(selected)) or not set(selected) <= set(tasks):
            raise ValueError(
                "correction subset must be unique, ordered and inside the frozen task set"
            )
        execution_manifest = {
            **manifest,
            "task_ids": selected,
            "tasks": [tasks[n] for n in selected],
        }
    if str(args.cowagent) not in sys.path:
        sys.path.insert(0, str(args.cowagent))
    from agent.tools.browser.browser_tool import BrowserTool

    args.output.mkdir(parents=True, mode=0o700)
    _write(args.output / "execution-inputs.json", execution_manifest)
    reset_sha256 = reset_cohort(args, manifest, sites)
    # Sites are restored before the measured reference window; static inputs
    # must still match the bytes validated before the reset hook ran.
    before = cohort_reference_hashes(args)
    if before != frozen_inputs:
        raise ValueError("Frozen cohort inputs changed during preflight or site reset")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    catalog = load_corrections()
    origins = {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
    results: list[dict] = []
    with (
        tempfile.TemporaryDirectory(prefix="three-method-browser-") as profile,
        tempfile.TemporaryDirectory(prefix="three-method-contracts-") as contract_root,
    ):
        contract_dir = Path(contract_root)
        candidate_paths: dict[int, Path] = {}
        for task_id, row in tasks.items():
            if "retrieval_contract" in row:
                candidate = contract_dir / f"task-{task_id}.json"
                _write(
                    candidate,
                    {
                        "task_id": task_id,
                        "tool_calls": row["source_actions"],
                        "retrieval_contract": row["retrieval_contract"],
                    },
                )
                candidate_paths[task_id] = candidate
        process = subprocess.Popen(
            _browser_command(args, port, profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_cdp(endpoint, process)
            readiness = verify_browser_session(
                BrowserTool(config=browser_tool_config(endpoint)), sites
            )
            _write(args.output / "browser-readiness.json", readiness)
            for task_id in execution_manifest["task_ids"]:
                result = _run_task(
                    task_id=task_id,
                    row=tasks[task_id],
                    benchmark=benchmark,
                    endpoint=endpoint,
                    output=args.output,
                    run_id=args.output.name,
                    origins=origins,
                    sites=sites,
                    catalog=catalog,
                    candidate_override_path=candidate_paths.get(task_id),
                )
                results.append(result)
                _write(args.output / "progress.json", results)
                print(
                    json.dumps(
                        {
                            key: value
                            for key, value in result.items()
                            if key not in {"error", "evidence_sha256"}
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    after = cohort_reference_hashes(args)
    verify_cohort_evidence(args.output, results, tasks)
    certified = certify_cohort(
        manifest=execution_manifest, rows=results, before=before, after=after, evidence_valid=True
    )
    receipt = {
        "schema": "webarena_three_method_cohort/1",
        "method_id": args.method_id,
        "engine": args.engine,
        "promotable": certified and not args.tasks,
        "execution_task_ids": execution_manifest["task_ids"],
        "correction_subset": bool(args.tasks),
        "certified": certified,
        "certification_independent_of_pass_rate": True,
        "run_count": 1,
        "retry_count": 0,
        "site_reset_between_tasks": False,
        "app_id": args.app_id,
        "browser_context_per_task": "fresh",
        "manifest_sha256": file_sha256(args.manifest),
        "dataset_sha256": file_sha256(args.dataset),
        "browser_binary_sha256": file_sha256(args.binary),
        "site_map_sha256": file_sha256(args.site_map),
        "cohort_reset_hook_sha256": file_sha256(args.cohort_reset_hook),
        "cohort_reset_sha256": reset_sha256,
        "reference_sha256_before": before,
        "reference_sha256_after": after,
        "declared_task_ids": manifest["task_ids"],
        "executed_task_ids": [row["task_id"] for row in results],
        "results": results,
    }
    _write(args.output / "receipt.json", receipt)
    return 0 if certified else 1


def execute_mutation_readiness(
    tool: Any, readiness: dict[str, Any], script: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    index = readiness["before_original_action"]
    arguments = {"action": "evaluate", "script": script}
    started = time.perf_counter()
    try:
        status, result = _execute_replay_call(tool, arguments)
    except Exception as exc:
        status, result = "error", f"{type(exc).__name__}: {exc}"
    row = {
        "index": f"before-{index}",
        "kind": "pre_action_readiness",
        "arguments": arguments,
        "status": status,
        "result": result,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if status != "success":
        row["validation_error"] = "BrowserTool readiness evaluate failed"
        return row, None
    try:
        observed = json.loads(result)
    except json.JSONDecodeError:
        row["validation_error"] = "Readiness result is not JSON"
        return row, None
    if readiness["kind"] == "same_origin_magento_product_type":
        proven = (
            isinstance(observed, dict) and observed.get("ready") is True
            and observed.get("productId") == readiness["product_id"]
            and observed.get("productType") == readiness["expected_type"]
            and observed.get("sourceCount") == 1
        )
        error = "Readiness result did not prove the frozen product identity and type"
    else:
        proven = (
            isinstance(observed, dict) and observed.get("ready") is True
            and observed.get("module") == readiness["module"]
            and observed.get("method") == readiness["deferred_method"]
            and isinstance(observed.get("after"), dict)
            and observed["after"].get("state") == "resolved"
        )
        error = "Readiness result did not prove resolved Deferred"
    if not proven:
        row["validation_error"] = error
        return row, observed if isinstance(observed, dict) else None
    return row, observed


def main_mutation_diagnostic(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--emit-proposal", type=Path)
    parser.add_argument("--check-proposal", action="store_true")
    parser.add_argument("--site-map", type=Path)
    parser.add_argument("--reset-receipt", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--cdp")
    parser.add_argument("--cowagent-repo", type=Path)
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.emit_proposal and args.check_proposal:
        parser.error("choose either --emit-proposal or --check-proposal")
    if args.emit_proposal:
        if args.proposal or args.output or args.cdp:
            parser.error("proposal emission cannot also execute a browser diagnostic")
    elif args.check_proposal:
        if not args.proposal or args.output or args.cdp:
            parser.error("proposal check needs --proposal and no browser execution arguments")
    else:
        physical = ("proposal", "site_map", "reset_receipt", "binary", "cdp",
                    "cowagent_repo", "output")
        missing = [name for name in physical if getattr(args, name) is None]
        if missing:
            parser.error(f"physical diagnostic requires: {', '.join(missing)}")
        if urlsplit(args.cdp).hostname not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("a dedicated local CDP endpoint is required")
    return run_mutation_diagnostic(args)


def run_mutation_diagnostic(args: argparse.Namespace) -> int:
    previous_path = list(sys.path)
    previous_mask = os.umask(0o077)
    try:
        return _execute_mutation_diagnostic(args)
    finally:
        sys.path[:] = previous_path
        os.umask(previous_mask)


def _execute_mutation_diagnostic(args: argparse.Namespace) -> int:
    dataset_path = (
        args.source_root / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
    )
    completion_path = args.source_root / COMPLETION_CANDIDATES
    input_paths = {
        "seeds_sha256": args.seeds, "dataset_sha256": dataset_path,
        "completion_candidates_sha256": completion_path,
    }
    if args.proposal is not None:
        input_paths["proposal_sha256"] = args.proposal
    if not args.emit_proposal and not args.check_proposal:
        input_paths.update({
            "binary_sha256": args.binary, "site_map_sha256": args.site_map,
            "reset_receipt_sha256": args.reset_receipt,
        })
    frozen_hashes = {name: checked_file_sha256(path, name) for name, path in input_paths.items()}

    def inputs_stable() -> bool:
        try:
            return all(checked_file_sha256(path, name) == frozen_hashes[name]
                       for name, path in input_paths.items())
        except (OSError, ValueError):
            return False

    manifest = json.loads(args.seeds.read_text(encoding="utf-8"))
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if dataset.get("provenance", {}).get("seed_manifest_sha256") != file_sha256(args.seeds):
        raise ValueError("qualified trajectories are not bound to these frozen seeds")
    task_seed = select_tasks(manifest, [args.task])[0]
    canonical = _candidate(task_seed)
    qualified = [row for row in dataset.get("trajectories", []) if row.get("task_id") == args.task]
    if len(qualified) != 1 or qualified[0].get("tool_calls") != canonical.get("tool_calls"):
        raise ValueError("selected task differs from its qualified trajectory")
    source_calls = canonical["tool_calls"]
    frozen_completion_sha256 = frozen_hashes["completion_candidates_sha256"]
    readiness, readiness_script = compile_mutation_readiness(
        completion_path, args.task, len(source_calls)
    )
    observer_rule, compiled_observer = compile_mutation_observer(
        completion_path, args.task, len(source_calls)
    )
    if not inputs_stable():
        raise ValueError("Frozen inputs changed during input compilation")
    if args.emit_proposal:
        if compiled_observer is None or observer_rule is None:
            raise ValueError("task has no compiled completion observer")
        proposal = {
            "task_id": args.task,
            "tool_calls": source_calls + [{"action": "evaluate", "script": compiled_observer}],
            "expected_completion": observer_rule["expected_completion"],
            "observer_contract_sha256": hashlib.sha256(compiled_observer.encode()).hexdigest(),
            "completion_candidates_sha256": frozen_completion_sha256,
        }
        path = args.emit_proposal.absolute()
        if path.exists() or path.is_symlink():
            raise ValueError("proposal output exists")
        _write(path, proposal)
        print(json.dumps({"status": "proposal_emitted", "path": str(path),
                          "sha256": file_sha256(path), "physical_calls": 0}))
        return 0
    proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
    proposed_calls = proposal.get("tool_calls")
    if (
        proposal.get("task_id") != args.task
        or not isinstance(proposed_calls, list)
        or proposed_calls[: len(source_calls)] != source_calls
        or len(proposed_calls) != len(source_calls) + 1
        or proposed_calls[-1].get("action") != "evaluate"
        or not isinstance(proposal.get("expected_completion"), str)
    ):
        raise ValueError("proposal must preserve the canonical actions and add one observer")
    verify_compiled_mutation_proposal(proposal, observer_rule, compiled_observer, completion_path)
    policy = mutation_observer_retry_policy(
        proposal.get("observer_retry_policy"), len(source_calls), compiled_observer
    )
    if not inputs_stable():
        raise ValueError("Frozen inputs changed during proposal validation")
    if args.check_proposal:
        print(json.dumps({"status": "proposal_valid", "task_id": args.task,
                          "proposal_sha256": file_sha256(args.proposal),
                          "completion_candidates_sha256": file_sha256(completion_path),
                          "pre_action_readiness": readiness is not None,
                          "physical_calls": 0}))
        return 0
    output = args.output.absolute()
    if output.is_symlink():
        raise ValueError("diagnostic output is symlinked")
    sites = load_remote_sites(args.site_map)
    benchmark = remote_benchmark(sites)
    task = benchmark.get_task(args.task)
    if not task.is_mutate_task:
        raise ValueError("this diagnostic accepts mutation tasks only")
    reset = validate_remote_reset_receipt(
        args.reset_receipt,
        required_sites=set(task.sites),
        site_map_sha256=file_sha256(args.site_map),
    )
    if reset["task_id"] != args.task:
        raise ValueError("reset receipt belongs to another task")
    evaluators = [item.model_dump(mode="json") for item in task.eval]
    if not any(item.get("evaluator") == "NetworkEventEvaluator" for item in evaluators):
        raise ValueError("mutation task has no official network event evaluator")
    if any(
        item.get("evaluator") not in {"NetworkEventEvaluator", "AgentResponseEvaluator"}
        for item in evaluators
    ):
        raise ValueError("mutation task has an unsupported official evaluator")
    calls = rebind_remote_calls(
        proposed_calls, sites, replay_site_hosts(source_calls)
    )
    starts = list(benchmark.config.render_url(list(task.start_urls), list(task.sites)))
    if not inputs_stable():
        raise ValueError("Frozen inputs changed during diagnostic preflight")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    if str(args.cowagent_repo.resolve()) not in sys.path:
        sys.path.insert(0, str(args.cowagent_repo.resolve()))
    recorder = CdpHarRecorder()
    har = output / "network.har"
    trace: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {
        "schema": "lex_webarena_remote_mutation_diagnostic/1",
        "run_id": uuid.uuid4().hex,
        "task_id": args.task,
        "scope": "diagnostic_only",
        "promotable": False,
        **frozen_hashes,
        "source_calls_sha256": _json_sha256(source_calls),
        "source_action_count": len(source_calls),
        "executed_action_count": len(calls),
        "pre_action_readiness": readiness,
        "pre_action_script_sha256": (
            hashlib.sha256(readiness_script.encode()).hexdigest()
            if readiness_script is not None else None
        ),
        "recorded_agent_response_used": False,
        "status": "running",
    }
    tool = None
    service = None
    started = time.perf_counter()
    try:
        from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

        tool = BrowserTool(config=browser_tool_config(args.cdp))
        _write(output / "receipt.json", receipt)
        service = tool._get_service()
        _clear_browser_state(
            service, {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
        )
        receipt["viewport"] = prepare_browser_viewport(service)
        receipt["login_retries"] = login_context(service, tuple(task.sites), benchmark, starts)
        service._submit(lambda: recorder.start(service._context))
        for index, arguments in enumerate(calls, 1):
            if readiness is not None and index == readiness["before_original_action"]:
                guard_row, observed_guard = execute_mutation_readiness(
                    tool, readiness, readiness_script
                )
                trace.append(guard_row)
                _write(output / "trace.json", trace)
                if observed_guard is not None:
                    _write(output / "pre-action-readiness.json", observed_guard)
                if guard_row.get("validation_error"):
                    raise RuntimeError(
                        f"pre-action readiness before {index} failed: "
                        f"{guard_row['validation_error']}"
                    )
            action_started = time.perf_counter()
            observer_attempts = []
            if index == len(calls) and policy:
                for number in range(1, policy["max_observer_attempts"] + 1):
                    status, result = _execute_replay_call(tool, arguments)
                    observer_attempts.append(
                        {"attempt": number, "status": status, "result": result}
                    )
                    if status == "success" or result != NAVIGATION_CONTEXT_LOSS:
                        break
                    if number < policy["max_observer_attempts"]:
                        service._submit(
                            lambda: service._page.wait_for_load_state(
                                "domcontentloaded",
                                timeout=policy["navigation_ready_timeout_ms"],
                            )
                        )
            else:
                status, result = _execute_replay_call(tool, arguments)
            trace.append({
                "index": index,
                "arguments": arguments,
                "status": status,
                "result": result,
                "observer_attempts": observer_attempts,
                "duration_ms": round((time.perf_counter() - action_started) * 1000, 3),
            })
            _write(output / "trace.json", trace)
            if status != "success":
                raise RuntimeError(f"replay action {index} failed")
        service._submit(lambda: recorder.stop(service._context, har))
        observed = json.loads(trace[-1]["result"])
        _write(output / "completion-observation.json", observed)
        if observed.get("completion") != proposal["expected_completion"]:
            raise RuntimeError("completion observer did not prove the expected saved state")
        response = {"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None}
        evaluation = benchmark.evaluate_task(
            task_id=args.task, agent_response=response, network_trace=har
        )
        _write(output / "evaluation.json", evaluation.model_dump(mode="json"))
        receipt["official_score"] = evaluation.score
        receipt["official_status"] = str(evaluation.status)
        receipt["status"] = (
            "diagnostic_pass" if evaluation.score == 1 and not recorder.errors
            else "diagnostic_fail"
        )
    except Exception:
        receipt["status"] = "diagnostic_incomplete"
        receipt["error"] = traceback.format_exc()
    finally:
        if service is not None:
            try:
                service._submit(lambda: recorder.stop(service._context, har))
            except Exception:
                pass
            try:
                service._submit(
                    lambda: service._page.screenshot(path=str(output / "last-page.png"))
                )
            except Exception:
                pass
        if tool is not None:
            try:
                tool.close()
            except Exception:
                receipt["status"] = "diagnostic_incomplete"
                receipt["cleanup_error"] = traceback.format_exc()
        receipt["completed_action_count"] = sum(
            row["status"] == "success" and row.get("kind") != "pre_action_readiness"
            for row in trace
        )
        receipt["input_hashes_stable"] = inputs_stable()
        if not receipt["input_hashes_stable"]:
            receipt["status"] = "diagnostic_incomplete"
            receipt["error"] = "Frozen inputs changed during the diagnostic"
        receipt["har_errors"] = recorder.errors
        receipt["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        receipt["evidence"] = {
            path.name: file_sha256(path)
            for path in output.iterdir()
            if path.is_file() and path.name != "receipt.json"
        }
        _write(output / "receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "output": str(output)}))
    return 0 if receipt["status"] == "diagnostic_pass" else 1
