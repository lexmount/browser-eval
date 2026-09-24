"""Replay one frozen WebArena catalog in its declared task order."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from harness.trajectory_replay.catalog_contracts import (
    RUN_SCHEMA,
    TRUSTED_RECORDED_HOSTS,
    completed_task_ids,
    file_sha256,
    load_catalog_inputs,
    repository_root,
    row_source_sites,
    validate_environment_preparation,
    verify_physical_evidence,
)
from harness.trajectory_replay.remote_execution import _browser_command, _run_task, _wait_cdp
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
        if len(set(selected)) != len(selected) or any(task_id not in declared for task_id in selected):
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--site-reset-receipt", type=Path, required=True)
    parser.add_argument("--environment-profile", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--engine", choices=("chromium", "moli"), default="chromium")
    parser.add_argument("--backend", choices=("browsertool", "playwright"), default="browsertool")
    parser.add_argument("--cowagent", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action-timeout-ms", type=int, default=30_000)
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
    verify_webarena_runtime_source()
    if args.action_timeout_ms < 1:
        raise ValueError("action timeout must be positive")
    if args.backend == "browsertool":
        if args.cowagent is None or not args.cowagent.is_dir():
            raise ValueError("BrowserTool backend requires --cowagent repository")
        if str(args.cowagent) not in sys.path:
            sys.path.insert(0, str(args.cowagent))
    catalog, sites, portable_tasks = load_catalog_inputs(
        args.catalog, args.portable_suite, args.site_map
    )
    validate_environment_preparation(args.site_reset_receipt, args.environment_profile)
    environment_profile = json.loads(args.environment_profile.read_text(encoding="utf-8"))
    corrections = load_corrections()
    benchmark = remote_benchmark(sites)
    origins = {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
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
        "binary_sha256": file_sha256(args.binary),
        "engine": args.engine,
        "backend": args.backend,
        "action_timeout_ms": args.action_timeout_ms,
        "corrections_sha256": corrections.source_sha256,
        "evaluator_fixes_sha256": file_sha256(
            root / "harness/trajectory_replay/webarena_evaluator_fixes.py"
        ),
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
        process = subprocess.Popen(
            _browser_command(browser_args, port, profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_cdp(endpoint, process)
            for frozen in pending:
                task_id = frozen["task_id"]
                source_actions = (
                    list(portable_tasks[task_id].tool_calls)
                    if frozen["mode"] == "portable"
                    else frozen["source_actions"]
                )
                row = {
                    **frozen,
                    "actions": rebind_remote_calls(
                        source_actions,
                        sites,
                        set(TRUSTED_RECORDED_HOSTS),
                        row_source_sites(catalog, frozen),
                    ),
                    "readiness": list(frozen.get("readiness") or []),
                }
                try:
                    outcome = _run_task(
                        task_id=task_id,
                        row=row,
                        benchmark=benchmark,
                        endpoint=endpoint,
                        output=args.output,
                        run_id=args.output.name,
                        origins=origins,
                        sites=sites,
                        catalog=corrections,
                        portable_task=(
                            portable_tasks[task_id] if frozen["mode"] == "portable" else None
                        ),
                        portable_action_timeout_ms=args.action_timeout_ms,
                        backend=args.backend,
                    )
                except FileExistsError:
                    raise
                except Exception:
                    outcome = {
                        "task_id": task_id,
                        "status": "candidate_setup_failure",
                        "error": traceback.format_exc(),
                    }
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
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
