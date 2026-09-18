"""Run a frozen original-failure candidate cohort against one remote WebArena slice.

The explicit formal mode binds all 36 inputs and evidence for the affected-task
cohort. Subset and candidate-override modes remain diagnostic only. Sites are
not reset between tasks; every task gets a fresh browser context, and accepted
answers come only from this run's BrowserTool results.
"""

from __future__ import annotations

import argparse
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
from urllib.parse import urlsplit

from webarena_verified.types.task import WebArenaSite

from harness.browser_use.har import CdpHarRecorder
from harness.browser_use.runner import (
    _browser_tool_config,
    _login_context,
    _prepare_browser_viewport,
)
from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
    validate_remote_cohort_reset_receipt,
)
from harness.trajectory_replay.retrieval_acceptance import build_actual_response
from harness.trajectory_replay.webarena_corrections import (
    apply_correction,
    check_replay_assertions,
    evaluate_corrected_contract,
    load_corrections,
)
from harness.trajectory_replay.webarena_replay import (
    _capture_failure_ui_state,
    _clear_browser_state,
    _click_was_dispatched_before_navigation_timeout,
    _execute_replay_call,
    _json_sha256,
)


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return file_sha256(path)


def _preflight(args: argparse.Namespace) -> tuple[dict, dict, dict]:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected_schema = (
        "original36_affected_cohort_inputs/1"
        if args.formal else "original36_qualified_candidate_cohort_inputs/1"
    )
    if manifest.get("schema") != expected_schema or manifest.get("promotable") is not args.formal:
        raise ValueError("frozen original36 manifest does not match the run mode")
    if args.formal and (
        args.engine != "moli" or args.tasks is not None
        or args.candidate_override is not None or args.qualification_plan is not None
        or args.diagnostic_variant is not None or args.cohort_reset_receipt is None
        or args.index is None or args.roles is None
    ):
        raise ValueError("formal affected cohort requires the complete frozen Moli scope")
    if args.formal and (
        file_sha256(args.index) != manifest["source_index_sha256"]
        or file_sha256(args.roles) != manifest["step_roles_sha256"]
    ):
        raise ValueError("formal source index or step roles changed")
    if args.engine == "moli":
        if file_sha256(args.binary) != manifest["moli_binary_sha256"]:
            raise ValueError("fixed Moli binary hash changed")
    elif (
        args.engine != "chromium"
        or not args.tasks
        or not args.candidate_override
        or not args.qualification_plan
    ):
        raise ValueError("Chromium is permitted only for one candidate qualification")
    if args.qualification_plan is not None:
        plan = json.loads(args.qualification_plan.read_text(encoding="utf-8"))
        expected = {
            "candidate_sha256": file_sha256(args.candidate_override),
            "frozen_cohort_input_sha256": file_sha256(args.manifest),
            "site_map_sha256": file_sha256(args.site_map),
            "browser_binary_sha256": file_sha256(args.binary),
            "runner_sha256": file_sha256(Path(__file__)),
            "remote_rebind_source_sha256": file_sha256(
                Path(__file__).resolve().parents[1]
                / "harness/trajectory_replay/remote_runtime.py"
            ),
            "cowagent_browser_tool_sha256": file_sha256(
                args.cowagent / "agent/tools/browser/browser_tool.py"
            ),
        }
        if (
            plan.get("schema") != "webarena_affected_task_candidate_qualification_plan/1"
            or plan.get("engine") != args.engine
            or plan.get("scope") != "one_changed_retrieval_candidate"
            or plan.get("promotable") is not False
            or plan.get("run_count") != 1
            or plan.get("retry_count") != 0
            or plan.get("site_reset") is not False
            or args.tasks != str(plan.get("task_id"))
            or any(plan.get(key) != value for key, value in expected.items())
        ):
            raise ValueError("candidate qualification plan changed")
    if file_sha256(args.site_map) != manifest["site_map_sha256"]:
        raise ValueError("remote site map hash changed")
    sites = load_remote_sites(args.site_map)
    if args.cohort_reset_receipt is not None:
        if args.app_id is None:
            raise ValueError("cohort reset receipt requires an explicit app ID")
        admin = urlsplit(sites[WebArenaSite.SHOPPING_ADMIN])
        validate_remote_cohort_reset_receipt(
            args.cohort_reset_receipt,
            site_map_sha256=manifest["site_map_sha256"],
            app_id=args.app_id,
            reset_scope="once_before_original36_candidate_cohort",
            admin_https_base_url=(
                f"{admin.scheme}://{admin.netloc}/" if admin.scheme == "https" else None
            ),
            required_readiness_probes=manifest.get("site_readiness"),
        )
    candidates = manifest["candidates"]
    ids = [row["task_id"] for row in candidates]
    if ids != manifest["candidate_task_ids"] or len(ids) != len(set(ids)) or len(ids) != 36:
        raise ValueError("frozen candidate cohort is incomplete or duplicated")
    if set(manifest["original_failed_task_ids"]) != set(ids) or manifest["quarantined"]:
        raise ValueError("original36 scope drift")
    for row in candidates:
        if _json_sha256(row["actions"]) != row["bound_calls_sha256"]:
            raise ValueError(f"task {row['task_id']}: bound calls changed")
        for readiness in row["readiness"]:
            if _json_sha256(readiness["input"]) != readiness["bound_input_sha256"]:
                raise ValueError(f"task {row['task_id']}: readiness call changed")
    if not args.cowagent.joinpath("agent/tools/browser/browser_tool.py").is_file():
        raise ValueError("CowAgent BrowserTool source is missing")
    if args.tasks:
        wanted = [int(value) for value in args.tasks.split(",")]
        if len(wanted) != len(set(wanted)) or not set(wanted) <= set(ids):
            raise ValueError("task selection must be unique and inside the frozen cohort")
        candidates = [row for row in candidates if row["task_id"] in wanted]
    if args.candidate_override is not None:
        if args.diagnostic_variant is not None:
            raise ValueError("use either a single candidate override or a diagnostic variant")
        if len(candidates) != 1:
            raise ValueError("an unqualified candidate override requires exactly one task")
        override = json.loads(args.candidate_override.read_text(encoding="utf-8"))
        row = candidates[0]
        if override.get("task_id") != row["task_id"] or not isinstance(
            override.get("retrieval_contract"), dict
        ):
            raise ValueError("candidate override must be a same-task retrieval contract")
        calls = override["tool_calls"]
        if args.qualification_plan is not None and len(calls) != plan["candidate_action_count"]:
            raise ValueError("candidate qualification action count changed")
        if override["retrieval_contract"].get("canonical_calls_sha256") != _json_sha256(calls):
            raise ValueError("candidate override contract is not bound to its calls")
        row = dict(row)
        row["actions"] = rebind_remote_calls(
            calls, sites, {"127.0.0.1", "192.168.5.2", "host.docker.internal"}
        )
        row["bound_calls_sha256"] = _json_sha256(row["actions"])
        row["source_calls_sha256"] = _json_sha256(calls)
        row["diagnostic_candidate_path"] = str(args.candidate_override)
        candidates = [row]
    if args.diagnostic_variant is not None:
        variant = json.loads(args.diagnostic_variant.read_text(encoding="utf-8"))
        if (
            variant.get("schema") != "webarena_original36_diagnostic_variant/1"
            or variant.get("promotable") is not False
        ):
            raise ValueError("diagnostic variant schema is invalid")
        by_task = {row["task_id"]: dict(row) for row in candidates}
        used = set()
        for override in variant["overrides"]:
            task_id = override["task_id"]
            if task_id in used or task_id not in by_task:
                raise ValueError("diagnostic variant has a duplicate or out-of-scope task")
            used.add(task_id)
            row = by_task[task_id]
            if override["kind"] == "retrieval_candidate":
                path = Path(override["path"])
                if path.is_symlink() or file_sha256(path) != override["sha256"]:
                    raise ValueError("diagnostic retrieval candidate changed")
                candidate = json.loads(path.read_text(encoding="utf-8"))
                if candidate.get("task_id") != task_id or candidate["retrieval_contract"][
                    "canonical_calls_sha256"
                ] != _json_sha256(candidate["tool_calls"]):
                    raise ValueError("diagnostic retrieval contract differs from calls")
                row["actions"] = rebind_remote_calls(
                    candidate["tool_calls"],
                    sites,
                    {"127.0.0.1", "192.168.5.2", "host.docker.internal"},
                )
                row["bound_calls_sha256"] = _json_sha256(row["actions"])
                row["source_calls_sha256"] = _json_sha256(candidate["tool_calls"])
                row["diagnostic_candidate_path"] = str(path)
            elif override["kind"] == "omit_trailing_diagnostic_observer":
                actions = row["actions"]
                if (
                    len(actions) != override["original_action_count"]
                    or override["action_count_after"] != len(actions) - 1
                    or actions[-1].get("action") != "evaluate"
                    or _json_sha256(actions[-1]) != override["trailing_action_sha256"]
                ):
                    raise ValueError("trailing diagnostic observer binding changed")
                row["actions"] = actions[:-1]
                row["bound_calls_sha256"] = _json_sha256(row["actions"])
                row["omitted_diagnostic_observer_sha256"] = override["trailing_action_sha256"]
            else:
                raise ValueError("unsupported diagnostic override kind")
        candidates = [by_task[row["task_id"]] for row in candidates]
    return manifest, sites, {row["task_id"]: row for row in candidates}


def _reference_hashes(args: argparse.Namespace) -> dict[str, str]:
    """Bind immutable task, evaluator and runner inputs around a formal cohort."""
    root = Path(__file__).resolve().parents[1]
    paths = {
        "manifest": args.manifest,
        "source_index": args.index,
        "step_roles": args.roles,
        "site_map": args.site_map,
        "moli_binary": args.binary,
        "cowagent_browser_tool": args.cowagent / "agent/tools/browser/browser_tool.py",
        "cowagent_browser_service": args.cowagent / "agent/tools/browser/browser_service.py",
        "task_selection": root / "evaluation/browser-use/webarena-verified-pilot.json",
        "frozen_trajectories": root / "evaluation/trajectory-replay/webarena-verified/trajectories.json",
        "task_corrections": root / "evaluation/trajectory-replay/webarena-verified/task-corrections.json",
        "runner": Path(__file__).resolve(),
        "replay_logic": root / "harness/trajectory_replay/webarena_replay.py",
        "retrieval_acceptance": root / "harness/trajectory_replay/retrieval_acceptance.py",
        "remote_runtime": root / "harness/trajectory_replay/remote_runtime.py",
    }
    for key, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"formal input is missing or symlinked: {key}")
    return {key: file_sha256(path) for key, path in paths.items()}


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
        return [
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
        candidate["tool_calls"],
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
) -> dict:
    from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

    official = benchmark.get_task(task_id)
    correction = apply_correction(official, catalog) if task_id in catalog.records else None
    tool = BrowserTool(config=_browser_tool_config(endpoint))
    recorder = CdpHarRecorder()
    task_dir = output / f"task-{task_id}"
    task_dir.mkdir(mode=0o700)
    har_path = task_dir / "network.har"
    records: list[dict] = []
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
        for index, action in enumerate(row["actions"], 1):
            for readiness in row["readiness"]:
                if readiness["before_action"] == index:
                    ready_status, ready_result = _execute_replay_call(tool, readiness["input"])
                    readiness_records.append(
                        {"before_action": index, "status": ready_status, "result": ready_result}
                    )
                    if ready_status != "success":
                        raise RuntimeError(f"readiness before step {index} failed: {ready_result}")
            call_started = time.perf_counter()
            result_status, result = _execute_replay_call(tool, action)
            dispatched = _click_was_dispatched_before_navigation_timeout(
                action, result_status, result
            )
            records.append(
                {
                    "index": index,
                    "arguments": action,
                    "status": "success" if dispatched else result_status,
                    "tool_status": result_status,
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "result": result,
                    **(
                        {"recovery": "click_dispatched_navigation_wait_timeout"}
                        if dispatched
                        else {}
                    ),
                }
            )
            if result_status != "success" and not dispatched:
                try:
                    records[-1]["failure_ui_state"] = _capture_failure_ui_state(service, action)
                except Exception as observation_exc:
                    records[-1]["failure_ui_state_error"] = (
                        f"{type(observation_exc).__name__}: {observation_exc}"
                    )[:300]
                raise RuntimeError(f"step {index} failed: {result}")
        if correction is not None and correction.replay_assertions:
            assertions = _evaluate_assertions(service, correction)
        else:
            assertions = []
        service._submit(lambda: service._page.wait_for_timeout(500))
        service._submit(lambda: recorder.stop(service._context, har_path))
        recorder_started = False
        hashes["network_har"] = file_sha256(har_path)
        if official.is_retrieve_task:
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
            error += f"\nBrowserTool close failed: {exc}"
    hashes["trace"] = _write(task_dir / "trace.json", records)
    hashes["readiness"] = _write(task_dir / "readiness.json", readiness_records)
    return {
        "task_id": task_id,
        "status": status,
        "score": score,
        "action_count": len(row["actions"]),
        "executed_actions_sha256": _json_sha256(row["actions"]),
        "omitted_diagnostic_observer_sha256": row.get("omitted_diagnostic_observer_sha256"),
        "completed_actions": sum(record["status"] == "success" for record in records),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "error": error,
        "evidence_sha256": hashes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--engine", choices=("moli", "chromium"), default="moli")
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--cowagent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--formal", action="store_true", help="certify one complete affected-task cohort")
    parser.add_argument("--index", type=Path, help="hash-bound source diagnosis index for a formal run")
    parser.add_argument("--roles", type=Path, help="hash-bound step roles for a formal run")
    parser.add_argument("--app-id", type=int, help="Explicit remote app identity")
    parser.add_argument(
        "--cohort-reset-receipt", type=Path, help="Evidence of one reset before the full cohort"
    )
    parser.add_argument("--tasks", help="Comma-separated diagnostic subset; default is all 36")
    parser.add_argument(
        "--candidate-override",
        type=Path,
        help="Single-task unqualified retrieval candidate; diagnostic only",
    )
    parser.add_argument("--qualification-plan", type=Path)
    parser.add_argument(
        "--diagnostic-variant",
        type=Path,
        help="Hash-bound unqualified cohort adjustments; diagnostic only",
    )
    args = parser.parse_args()
    if args.tasks is None and args.cohort_reset_receipt is None:
        raise ValueError("full cohort requires a pre-cohort reset receipt")
    manifest, sites, selected = _preflight(args)
    reference_before = _reference_hashes(args) if args.formal else None
    if args.output.exists():
        raise ValueError("output exists; evidence is append-only")
    if str(args.cowagent) not in sys.path:
        sys.path.insert(0, str(args.cowagent))
    os.umask(0o077)
    args.output.mkdir(parents=True, mode=0o700)
    run_id = args.output.name
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    benchmark = remote_benchmark(sites)
    catalog = load_corrections()
    origins = {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
    rows = []
    with tempfile.TemporaryDirectory(prefix="moli-candidate-cohort-") as profile:
        process = subprocess.Popen(
            _browser_command(args, port, profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_cdp(endpoint, process)
            for task_id, row in selected.items():
                result = _run_task(
                    task_id=task_id,
                    row=row,
                    benchmark=benchmark,
                    endpoint=endpoint,
                    output=args.output,
                    run_id=run_id,
                    origins=origins,
                    sites=sites,
                    catalog=catalog,
                    candidate_override_path=(
                        Path(row["diagnostic_candidate_path"])
                        if row.get("diagnostic_candidate_path")
                        else None
                    ),
                )
                rows.append(result)
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
                _write(args.output / "progress.json", rows)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    reference_after = _reference_hashes(args) if args.formal else None
    if args.formal:
        _verify_result_evidence(args.output, rows)
    complete = (
        len(rows) == 36
        and [row["task_id"] for row in rows] == manifest["candidate_task_ids"]
        and all(row["status"] == "pass" and row["score"] == 1.0 for row in rows)
    )
    reference_unchanged = reference_before is not None and reference_before == reference_after
    receipt = {
        "schema": (
            "original36_remote_affected_cohort/1"
            if args.formal else "original36_remote_candidate_cohort_diagnostic/1"
        ),
        "promotable": args.formal and complete and reference_unchanged,
        "site_reset_between_tasks": False,
        "formal_reference_pre_post_check": reference_unchanged,
        "reference_scope": (
            "immutable_task_evaluator_runner_and_browser_inputs; live_site_state_may_change"
            if args.formal else None
        ),
        "reference_sha256_before": reference_before,
        "reference_sha256_after": reference_after,
        "manifest_sha256": file_sha256(args.manifest),
        "unqualified_candidate_override_sha256": (
            file_sha256(args.candidate_override) if args.candidate_override else None
        ),
        "diagnostic_variant_sha256": (
            file_sha256(args.diagnostic_variant) if args.diagnostic_variant else None
        ),
        "browser_binary_sha256": file_sha256(args.binary),
        "moli_binary_sha256": file_sha256(args.binary) if args.engine == "moli" else None,
        "engine": args.engine,
        "site_map_sha256": file_sha256(args.site_map),
        "cohort_reset_receipt_sha256": (
            file_sha256(args.cohort_reset_receipt) if args.cohort_reset_receipt else None
        ),
        "qualification_plan_sha256": (
            file_sha256(args.qualification_plan) if args.qualification_plan else None
        ),
        "cowagent_browser_tool_sha256": file_sha256(
            args.cowagent / "agent/tools/browser/browser_tool.py"
        ),
        "cowagent_browser_service_sha256": file_sha256(
            args.cowagent / "agent/tools/browser/browser_service.py"
        ),
        "declared_task_ids": manifest["candidate_task_ids"],
        "executed_task_ids": list(selected),
        "results": rows,
    }
    _write(args.output / "receipt.json", receipt)
    return 0 if (receipt["promotable"] if args.formal else all(row["status"] == "pass" for row in rows)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
