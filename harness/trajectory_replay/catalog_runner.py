"""Replay one frozen WebArena catalog in its declared task order."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import quote, urlsplit

from harness.trajectory_replay.catalog_contracts import (
    RUN_SCHEMA,
    TRUSTED_RECORDED_HOSTS,
    apply_action_overrides,
    catalog_action_overrides,
    completed_task_ids,
    file_sha256,
    load_catalog_inputs,
    repository_root,
    row_source_sites,
    validate_environment_preparation,
    verify_physical_evidence,
)
from harness.trajectory_replay.remote_execution import (
    _browser_command,
    _run_task,
    _wait_cdp,
    finalize_timed_out_task,
    sanitize_remote_text,
)
from harness.trajectory_replay.remote_runtime import rebind_remote_calls, remote_benchmark
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_runtime_source import verify_webarena_runtime_source


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(row, ensure_ascii=False) + "\n")
        target.flush()
        os.fsync(target.fileno())


def _task_ids(
    declared: list[int],
    task_from: int | None,
    task_to: int | None,
    selected: list[int] | None = None,
) -> list[int]:
    if not declared or len(set(declared)) != len(declared):
        raise ValueError("catalog task order is empty or contains duplicates")
    if selected:
        if task_from is not None or task_to is not None:
            raise ValueError("explicit task IDs cannot be combined with a task range")
        if len(set(selected)) != len(selected) or any(
            task_id not in declared for task_id in selected
        ):
            raise ValueError("explicit task IDs must be unique catalog members")
        expected = [task_id for task_id in declared if task_id in set(selected)]
        if selected != expected:
            raise ValueError("explicit task IDs must follow catalog order")
        return selected
    lower = declared[0] if task_from is None else task_from
    upper = declared[-1] if task_to is None else task_to
    if lower not in declared or upper not in declared:
        raise ValueError("task range endpoints must belong to the catalog")
    start = declared.index(lower)
    end = declared.index(upper)
    if start > end:
        raise ValueError("task range is reversed in catalog order")
    return declared[start : end + 1]


def _task_origins(benchmark: object, task_id: int, sites: dict) -> set[str]:
    """Return only the fixture origins declared by the current official task.

    Browsers without ``Storage.clearDataForOrigin`` clear site storage by
    visiting each supplied origin. Passing the whole deployment makes an
    unrelated site outage invalidate every task in the cohort and does extra
    cross-site work before each replay.
    """

    task = benchmark.get_task(task_id)
    return {
        f"{urlsplit(sites[site]).scheme}://{urlsplit(sites[site]).netloc}"
        for site in task.sites
    }


def _recover_completed_attempts(output: Path, progress: Path, ids: list[int]) -> set[int]:
    """Commit a finished result left between task completion and progress append."""

    done = completed_task_ids(progress, ids)
    while len(done) < len(ids):
        task_id = ids[len(done)]
        task_dir = output / f"task-{task_id}"
        if not task_dir.exists():
            break
        result_path = task_dir / "result.json"
        if result_path.is_symlink() or not result_path.is_file():
            raise ValueError(
                f"task {task_id} has interrupted evidence without a committed result; "
                "use a fresh prepared deployment before retrying"
            )
        row = json.loads(result_path.read_text(encoding="utf-8"))
        if row.get("task_id") != task_id:
            raise ValueError(f"task {task_id} committed result identity changed")
        verify_physical_evidence(output, row)
        _append(progress, row)
        done.add(task_id)
    return done


def _load_task_selection(path: Path) -> list[int]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("task selection must be a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    task_ids = payload.get("task_ids") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "lex_webarena_replay_selection/1"
        or not isinstance(task_ids, list)
        or not task_ids
        or any(type(task_id) is not int for task_id in task_ids)
    ):
        raise ValueError("task selection contract is invalid")
    return task_ids


def _load_env_values(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("credential environment file must be a regular file")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
    return values


def _kitesurf_cdp_config(
    path: Path,
) -> tuple[str, dict[str, str], dict[str, object]]:
    values = _load_env_values(path)
    account_id = values.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = values.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise ValueError(
            "Kitesurf requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN"
        )
    endpoint = (
        "wss://api.cloudflare.com/client/v4/accounts/"
        f"{quote(account_id, safe='')}"
        "/browser-run/devtools/browser?browser=kitesurf"
    )
    headers = {"Authorization": f"Bearer {api_token}"}
    public_binding = {
        "provider": "cloudflare-browser-run",
        "browser": "kitesurf",
        "protocol": "cdp",
        "endpoint_contract": (
            "wss://api.cloudflare.com/client/v4/accounts/{account_id}/"
            "browser-run/devtools/browser?browser=kitesurf"
        ),
        "account_id_sha256": hashlib.sha256(account_id.encode()).hexdigest(),
    }
    return endpoint, headers, public_binding


def _probe_kitesurf_runtime(
    endpoint: str, headers: dict[str, str], *, timeout_ms: int = 120_000
) -> dict[str, str]:
    """Freeze the hosted product/version identity before any task evidence exists."""

    def probe() -> dict[str, str]:
        from harness.trajectory_replay.playwright_tool import (  # noqa: PLC0415
            PlaywrightReplayTool,
        )

        tool = PlaywrightReplayTool(endpoint, engine="kitesurf", headers=headers)
        try:
            metadata = tool.runtime_metadata
            if not isinstance(metadata, dict):
                raise RuntimeError("Kitesurf did not expose Browser.getVersion metadata")
            return metadata
        finally:
            tool.close()

    return _run_forked_with_watchdog(probe, timeout_ms=timeout_ms)


def _chromium_host_resolver_rules(environment_profile: dict, engine: str) -> str | None:
    """Compile deployment-neutral host overrides for Chromium, with legacy read support."""

    if engine != "chromium":
        return None
    overrides = environment_profile.get("host_overrides")
    if overrides is None:
        legacy = environment_profile.get("browser_host_resolver_rules")
        if legacy is not None and (not isinstance(legacy, str) or "\n" in legacy):
            raise ValueError("legacy Chromium host resolver rules must be one line")
        return legacy
    if not isinstance(overrides, list) or not overrides:
        raise ValueError("environment host overrides must be a nonempty list")
    rules = []
    for row in overrides:
        pattern = row.get("pattern") if isinstance(row, dict) else None
        address = row.get("address") if isinstance(row, dict) else None
        if (
            not isinstance(pattern, str)
            or not pattern
            or any(char in pattern for char in "\n,")
            or not isinstance(address, str)
            or not address
            or any(char in address for char in "\n,")
        ):
            raise ValueError("environment host override is invalid")
        rules.append(f"MAP {pattern} {address}")
    return ", ".join(rules)


def _request_bridge_options(
    environment_profile: dict,
    engine: str,
    task_origins: set[str],
) -> dict[str, object]:
    """Bind Kitesurf's local bridge to the deployment's declared host routing."""

    if engine != "kitesurf":
        return {
            "request_bridge_origins": None,
            "request_bridge_host_overrides": None,
        }
    return {
        "request_bridge_origins": task_origins,
        "request_bridge_host_overrides": environment_profile.get("host_overrides") or [],
    }


def _stop_browser(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _run_with_setup_recovery(
    *,
    task_id: int,
    task_dir: Path,
    process: subprocess.Popen,
    restart_browser,
    run_task,
    row: dict | None = None,
    task_timeout_ms: int | None = None,
) -> tuple[dict, subprocess.Popen]:
    """Run one task, restarting only before physical evidence exists.

    ``_run_task`` creates ``task_dir`` immediately before browser-session work and
    catches all later execution failures into a committed result. An exception
    with no task directory is therefore a setup failure that is safe to retry;
    once the directory exists, retrying could duplicate a physical execution.
    """

    def run_once(current: subprocess.Popen) -> dict:
        if row is None or task_timeout_ms is None:
            return run_task()
        state = {"finished": False, "timed_out": False}
        lock = threading.Lock()

        def expire() -> None:
            with lock:
                if state["finished"]:
                    return
                state["timed_out"] = True
            _stop_browser(current)

        started = time.monotonic()
        timer = threading.Timer(task_timeout_ms / 1_000, expire)
        timer.daemon = True
        timer.start()
        error: Exception | None = None
        result: dict | None = None
        try:
            result = run_task()
        except Exception as exc:
            error = exc
        finally:
            with lock:
                state["finished"] = True
                timed_out = state["timed_out"]
            timer.cancel()
            timer.join()
        if timed_out:
            if not task_dir.is_dir():
                raise TimeoutError(
                    f"task {task_id} exceeded its {task_timeout_ms} ms budget before evidence began"
                )
            return finalize_timed_out_task(
                task_dir,
                row,
                duration_ms=max(task_timeout_ms, int((time.monotonic() - started) * 1_000)),
                task_timeout_ms=task_timeout_ms,
            )
        if error is not None:
            raise error
        assert result is not None
        return result

    runtime = {"process": process}

    def execute() -> dict:
        current = runtime["process"]
        if current.poll() is not None:
            current = restart_browser(current)
            runtime["process"] = current
        return run_once(current)

    def recover() -> None:
        runtime["process"] = restart_browser(runtime["process"])

    return (
        _run_with_pre_evidence_recovery(
            task_id=task_id,
            task_dir=task_dir,
            run_once=execute,
            recover=recover,
        ),
        runtime["process"],
    )


def _run_with_pre_evidence_recovery(
    *, task_id: int, task_dir: Path, run_once, recover
) -> dict:
    """Retry once only when setup failed before any physical evidence existed."""

    try:
        return run_once()
    except FileExistsError:
        raise
    except Exception:
        first_error = traceback.format_exc()
        if task_dir.exists():
            (task_dir / ".trace.partial.json").unlink(missing_ok=True)
            return {
                "task_id": task_id,
                "status": "candidate_setup_failure",
                "error": first_error,
            }
    recover()
    try:
        return run_once()
    except FileExistsError:
        raise
    except Exception:
        if task_dir.exists():
            (task_dir / ".trace.partial.json").unlink(missing_ok=True)
        return {
            "task_id": task_id,
            "status": "candidate_setup_failure",
            "error": f"{first_error}\nsetup recovery failed:\n{traceback.format_exc()}",
        }


def _fork_worker(sender, run_task) -> None:
    try:
        sender.send(("result", run_task()))
    except BaseException:
        sender.send(("error", traceback.format_exc()))
    finally:
        sender.close()


def _run_forked_with_watchdog(run_task, *, timeout_ms: int) -> dict:
    """Run one hosted-browser operation behind a killable process boundary."""

    if "fork" not in multiprocessing.get_all_start_methods():
        raise RuntimeError("hosted browser replay requires a POSIX fork process boundary")
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_fork_worker, args=(sender, run_task), daemon=False)
    worker.start()
    sender.close()
    try:
        if not receiver.poll(timeout_ms / 1_000):
            worker.terminate()
            worker.join(timeout=10)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=10)
            raise TimeoutError(f"hosted browser operation exceeded {timeout_ms} ms")
        kind, payload = receiver.recv()
        worker.join(timeout=10)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=10)
            raise RuntimeError("hosted browser worker did not exit after returning a result")
        if kind == "error":
            raise RuntimeError(payload)
        if worker.exitcode != 0:
            raise RuntimeError(f"hosted browser worker exited with code {worker.exitcode}")
        return payload
    finally:
        receiver.close()


def _run_remote_with_setup_recovery(
    *,
    task_id: int,
    task_dir: Path,
    row: dict,
    task_timeout_ms: int,
    runtime_metadata: dict[str, str],
    remote_endpoint: str,
    remote_headers: dict[str, str],
    run_task,
) -> dict:
    """Run a hosted task with the shared setup retry and a whole-task watchdog."""

    def run_once() -> dict:
        started = time.monotonic()
        try:
            return _run_forked_with_watchdog(run_task, timeout_ms=task_timeout_ms)
        except TimeoutError:
            if not task_dir.is_dir():
                raise
            return finalize_timed_out_task(
                task_dir,
                row,
                duration_ms=max(
                    task_timeout_ms, int((time.monotonic() - started) * 1_000)
                ),
                task_timeout_ms=task_timeout_ms,
                runtime_metadata=runtime_metadata,
                remote_endpoint=remote_endpoint,
                remote_headers=remote_headers,
            )

    return _run_with_pre_evidence_recovery(
        task_id=task_id,
        task_dir=task_dir,
        run_once=run_once,
        recover=lambda: None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--action-overrides", type=Path)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--site-reset-receipt", type=Path, required=True)
    parser.add_argument("--environment-profile", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument(
        "--engine",
        choices=("chromium", "moli", "lightpanda", "obscura", "kitesurf"),
        default="chromium",
    )
    parser.add_argument(
        "--credential-env-file",
        type=Path,
        default=repository_root(Path(__file__)) / ".env",
        help="local credential source for a remote browser; values are never persisted",
    )
    parser.add_argument("--backend", choices=("browsertool", "playwright"), default="browsertool")
    parser.add_argument("--cowagent", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action-timeout-ms", type=int, default=30_000)
    parser.add_argument(
        "--task-timeout-ms",
        type=int,
        default=600_000,
        help=(
            "per-task execution budget; local engines additionally terminate an "
            "unresponsive browser process"
        ),
    )
    parser.add_argument("--task-from", type=int)
    parser.add_argument("--task-to", type=int)
    parser.add_argument(
        "--task-id",
        type=int,
        action="append",
        help="replay only these catalog tasks, supplied once each in catalog order",
    )
    parser.add_argument(
        "--task-selection",
        type=Path,
        help="JSON selection for a large unresolved-only replay",
    )
    args = parser.parse_args()
    webarena_runtime = verify_webarena_runtime_source()
    if args.action_timeout_ms < 1:
        raise ValueError("action timeout must be positive")
    if args.task_timeout_ms < 1:
        raise ValueError("task timeout must be positive")
    if args.engine == "kitesurf":
        if args.backend != "playwright":
            raise ValueError("Kitesurf trajectory replay requires the Playwright backend")
        if args.binary is not None:
            raise ValueError("Kitesurf is remote and does not accept --binary")
        remote_endpoint, remote_headers, remote_binding = _kitesurf_cdp_config(
            args.credential_env_file.expanduser()
        )
        try:
            remote_binding["runtime_metadata"] = _probe_kitesurf_runtime(
                remote_endpoint, remote_headers
            )
        except Exception as exc:
            raise RuntimeError(
                sanitize_remote_text(str(exc), remote_endpoint, remote_headers)
            ) from None
    else:
        if args.binary is None:
            raise ValueError("local replay engines require a browser --binary")
        remote_endpoint = None
        remote_headers = None
        remote_binding = None
    if args.backend == "browsertool":
        if args.cowagent is None or not args.cowagent.is_dir():
            raise ValueError("BrowserTool backend requires --cowagent repository")
        if str(args.cowagent) not in sys.path:
            sys.path.insert(0, str(args.cowagent))
    catalog, sites, portable_tasks = load_catalog_inputs(
        args.catalog,
        args.portable_suite,
        args.site_map,
        action_overrides_path=args.action_overrides,
    )
    if args.action_overrides is None and (
        catalog.get("action_overrides_sha256") is not None
        or any(row.get("action_overrides") is not None for row in catalog.get("rows") or [])
    ):
        raise ValueError("catalog requires its versioned action overrides input")
    validate_environment_preparation(args.site_reset_receipt, args.environment_profile)
    environment_profile = json.loads(args.environment_profile.read_text(encoding="utf-8"))
    corrections = load_corrections()
    benchmark = remote_benchmark(sites)
    if args.task_selection is not None and args.task_id:
        raise ValueError("task selection file cannot be combined with explicit task IDs")
    selected = _load_task_selection(args.task_selection) if args.task_selection else args.task_id
    ids = _task_ids(list(catalog["task_ids"]), args.task_from, args.task_to, selected)
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    root = repository_root(Path(__file__))
    binding = {
        "schema": RUN_SCHEMA,
        "catalog_sha256": file_sha256(args.catalog),
        "portable_suite_sha256": file_sha256(args.portable_suite),
        "site_map_sha256": file_sha256(args.site_map),
        "site_reset_receipt_sha256": file_sha256(args.site_reset_receipt),
        "environment_profile_sha256": file_sha256(args.environment_profile),
        "binary_sha256": file_sha256(args.binary) if args.binary is not None else None,
        "engine": args.engine,
        "backend": args.backend,
        "action_timeout_ms": args.action_timeout_ms,
        "task_timeout_ms": args.task_timeout_ms,
        "corrections_sha256": corrections.source_sha256,
        "webarena_revision": webarena_runtime.revision,
        "runner_revision": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "cowagent_revision": (
            subprocess.check_output(
                ["git", "-C", str(args.cowagent), "rev-parse", "HEAD"], text=True
            ).strip()
            if args.backend == "browsertool"
            else None
        ),
        "execution_order": ids,
        "site_reset_between_tasks": False,
        "run_count_per_task": 1,
        "task_selection_sha256": (
            file_sha256(args.task_selection) if args.task_selection is not None else None
        ),
    }
    if remote_binding is not None:
        binding["remote_browser"] = remote_binding
        binding["request_bridge"] = {
            "kind": "local_http_fulfill/1",
            "origins": sorted(
                {
                    f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
                    for url in sites.values()
                }
            ),
        }
    if args.action_overrides is not None:
        binding["action_overrides_sha256"] = file_sha256(args.action_overrides)
    binding_path = args.output / "inputs.json"
    if binding_path.exists():
        if json.loads(binding_path.read_text(encoding="utf-8")) != binding:
            raise ValueError("resumed run inputs changed")
    else:
        binding_path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    progress = args.output / "progress.jsonl"
    done = _recover_completed_attempts(args.output, progress, ids)
    pending = [
        row for row in catalog["rows"] if row["task_id"] in ids and row["task_id"] not in done
    ]
    if not pending:
        return

    def bound_row(frozen: dict) -> dict:
        task_id = frozen["task_id"]
        source_actions = (
            list(portable_tasks[task_id].tool_calls)
            if frozen["mode"] == "portable"
            else frozen["source_actions"]
        )
        source_actions = apply_action_overrides(
            source_actions,
            catalog_action_overrides(catalog, frozen),
            task_id=task_id,
        )
        return {
            **frozen,
            "actions": rebind_remote_calls(
                source_actions,
                sites,
                set(TRUSTED_RECORDED_HOSTS),
                row_source_sites(catalog, frozen),
            ),
            "readiness": list(frozen.get("readiness") or []),
        }

    def execute_task(
        frozen: dict,
        row: dict,
        endpoint: str,
        cdp_headers: dict[str, str] | None = None,
    ) -> dict:
        task_id = frozen["task_id"]
        task_origins = _task_origins(benchmark, task_id, sites)
        return _run_task(
            task_id=task_id,
            row=row,
            benchmark=benchmark,
            endpoint=endpoint,
            output=args.output,
            run_id=args.output.name,
            origins=task_origins,
            sites=sites,
            catalog=corrections,
            portable_task=(
                portable_tasks[task_id] if frozen["mode"] == "portable" else None
            ),
            portable_action_timeout_ms=args.action_timeout_ms,
            backend=args.backend,
            engine=args.engine,
            cdp_headers=cdp_headers,
            expected_runtime_metadata=(
                remote_binding["runtime_metadata"]
                if remote_binding is not None
                else None
            ),
            **_request_bridge_options(environment_profile, args.engine, task_origins),
            task_timeout_ms=args.task_timeout_ms,
        )

    def record_outcome(outcome: dict) -> None:
        _append(progress, outcome)
        print(
            json.dumps(
                {
                    key: value
                    for key, value in outcome.items()
                    if key not in {"error", "evidence_sha256"}
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    if remote_endpoint is not None:
        assert remote_headers is not None
        for frozen in pending:
            task_id = frozen["task_id"]
            row = bound_row(frozen)
            task_dir = args.output / f"task-{task_id}"
            outcome = _run_remote_with_setup_recovery(
                task_id=task_id,
                task_dir=task_dir,
                row=row,
                task_timeout_ms=args.task_timeout_ms,
                runtime_metadata=remote_binding["runtime_metadata"],
                remote_endpoint=remote_endpoint,
                remote_headers=remote_headers,
                run_task=lambda frozen=frozen, row=row: execute_task(
                    frozen, row, remote_endpoint, remote_headers
                ),
            )
            if outcome.get("error"):
                outcome["error"] = sanitize_remote_text(
                    str(outcome["error"]), remote_endpoint, remote_headers
                )
            record_outcome(outcome)
        return

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    browser_args = argparse.Namespace(
        engine=args.engine,
        binary=args.binary,
        host_resolver_rules=_chromium_host_resolver_rules(environment_profile, args.engine),
    )
    with tempfile.TemporaryDirectory(prefix="webarena-catalog-") as profile:
        def start_browser() -> subprocess.Popen:
            started = subprocess.Popen(
                _browser_command(browser_args, port, profile),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                _wait_cdp(endpoint, started)
            except Exception:
                _stop_browser(started)
                raise
            return started

        def restart_browser(current: subprocess.Popen) -> subprocess.Popen:
            _stop_browser(current)
            return start_browser()

        process = start_browser()
        try:
            for pending_index, frozen in enumerate(pending):
                task_id = frozen["task_id"]
                row = bound_row(frozen)
                def run_task(
                    row: dict = row,
                    frozen: dict = frozen,
                ) -> dict:
                    return execute_task(frozen, row, endpoint)

                outcome, process = _run_with_setup_recovery(
                    task_id=task_id,
                    task_dir=args.output / f"task-{task_id}",
                    process=process,
                    restart_browser=restart_browser,
                    run_task=run_task,
                    row=row,
                    task_timeout_ms=args.task_timeout_ms,
                )
                record_outcome(outcome)
                if (
                    outcome.get("runtime_poisoned") or process.poll() is not None
                ) and pending_index + 1 < len(pending):
                    process = restart_browser(process)
        finally:
            _stop_browser(process)


if __name__ == "__main__":
    main()
