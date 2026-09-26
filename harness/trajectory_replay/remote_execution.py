"""Shared browser launch, task execution and evidence verification for remote replay."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

from webarena_verified.types.task import WebArenaSite

from harness.browser_use.har import CdpHarRecorder
from harness.browser_use.runner import (
    _browser_tool_config,
    _login_context,
    _prepare_browser_viewport,
)
from harness.trajectory_replay.catalog_contracts import (
    apply_action_overrides,
    catalog_action_overrides,
)
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.public_webarena.response_builder import build_agent_response
from harness.trajectory_replay.public_webarena.suite import BrowserTask
from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    rebind_remote_calls,
)
from harness.trajectory_replay.retrieval_acceptance import build_actual_response
from harness.trajectory_replay.webarena_corrections import (
    apply_correction,
    check_replay_assertions,
    evaluate_corrected_contract,
)
from harness.trajectory_replay.webarena_replay import (
    _capture_failure_ui_state,
    _clear_browser_state,
    _click_was_dispatched_before_navigation_timeout,
    _execute_replay_call,
    _json_sha256,
    _replay_step_status,
)

NETWORK_SETTLE_MS = 2_000


def _effective_retrieval_calls(task_id: int, row: dict, calls: list[dict]) -> list[dict]:
    """Apply the catalog's frozen amendments without changing contract source calls."""

    provenance = row.get("action_overrides")
    catalog = {
        "action_overrides_sha256": (
            provenance.get("source_sha256") if isinstance(provenance, dict) else None
        )
    }
    return apply_action_overrides(
        calls,
        catalog_action_overrides(catalog, row),
        task_id=task_id,
    )


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
            raise RuntimeError(f"Moli exited before CDP was ready: {process.returncode}")
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
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
        {"127.0.0.1", "192.168.5.2", "host.docker.internal"},
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
) -> dict:
    official = benchmark.get_task(task_id)
    correction = apply_correction(official, catalog) if task_id in catalog.records else None
    if backend == "browsertool":
        from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

        tool = BrowserTool(config=_browser_tool_config(endpoint))
    elif backend == "playwright":
        from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

        tool = PlaywrightReplayTool(endpoint, engine=engine)
    else:
        raise ValueError(f"unknown replay backend: {backend}")
    recorder = CdpHarRecorder()
    task_dir = output / f"task-{task_id}"
    task_dir.mkdir(mode=0o700)
    har_path = task_dir / "network.har"
    records: list[dict] = []
    portable_outputs: dict[str, str] = {}
    readiness_records: list[dict] = []
    hashes: dict[str, str] = {}
    started = time.perf_counter()
    status = "fail"
    score = None
    error = ""
    service = None
    recorder_started = False
    try:
        service = tool._get_service()
        service._submit(lambda: None)
        viewport = _prepare_browser_viewport(service)
        _clear_browser_state(service, origins)
        start_urls = list(
            benchmark.config.render_url(list(official.start_urls), list(official.sites))
        )
        _login_context(service, tuple(official.sites), benchmark, start_urls, viewport)
        service._submit(lambda: recorder.start(service._context))
        recorder_started = True
        if portable_task is not None:
            service._submit(
                lambda: execute_steps(
                    service._page,
                    row["actions"],
                    trace=records,
                    outputs=portable_outputs,
                    task_timeout_ms=300_000,
                    default_action_timeout_ms=portable_action_timeout_ms,
                    layout_observer=getattr(service, "layout_observer", None),
                    navigation_response_observer=recorder.record_navigation_response,
                )
            )
        for index, action in enumerate(() if portable_task is not None else row["actions"], 1):
            for readiness in row["readiness"]:
                if readiness["before_action"] == index:
                    ready_status, ready_result = _execute_replay_call(tool, readiness["input"])
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
            result_status, result = _execute_replay_call(tool, action, diagnostics=diagnostics)
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
                try:
                    records[-1]["failure_ui_state"] = _capture_failure_ui_state(service, action)
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
        if service is not None and recorder_started:
            try:
                service._submit(lambda: recorder.stop(service._context, har_path))
                hashes["network_har"] = file_sha256(har_path)
            except Exception:
                pass
        if service is not None:
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
    hashes["trace"] = _write(task_dir / "trace.json", records)
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
