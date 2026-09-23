"""Replay one frozen 812-case WebArena catalog in official task order."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_execution import _browser_command, _run_task, _wait_cdp
from harness.trajectory_replay.remote_runtime import (
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
)
from harness.trajectory_replay.retrieval_acceptance import validate_contract
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_replay import _json_sha256

SCHEMA = "lex_webarena_full_replay_catalog/1"
TRUSTED_RECORDED_HOSTS = frozenset(
    {
        "onestopmarket.example.com",
        "gitlab.example.com",
        "openstreetmap.example.com",
        "wikipedia.example.com",
        "ec2-3-133-227-75.us-east-2.compute.amazonaws.com",
        "ec2-3-135-39-80.us-east-2.compute.amazonaws.com",
        "host.docker.internal",
        "192.168.5.2",
        "127.0.0.1",
    }
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(row, ensure_ascii=False) + "\n")
        target.flush()
        os.fsync(target.fileno())


def _completed(path: Path, ids: list[int]) -> set[int]:
    if not path.exists():
        return set()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    seen: set[int] = set()
    for row in rows:
        task_id = row.get("task_id")
        if task_id not in ids or task_id in seen:
            raise ValueError("progress contains unknown or duplicate task ID")
        seen.add(task_id)
    if [row["task_id"] for row in rows] != [task_id for task_id in ids if task_id in seen]:
        raise ValueError("progress changed the official case order")
    return seen


def _task_ids(task_from: int, task_to: int) -> list[int]:
    if not 0 <= task_from <= task_to < 812:
        raise ValueError("task range must be within the official 0..811 order")
    return list(range(task_from, task_to + 1))


def _source_sites(catalog: dict) -> dict[WebArenaSite, str]:
    raw = catalog.get("source_site_urls") or {}
    if not isinstance(raw, dict) or not set(raw) <= {site.value for site in WebArenaSite}:
        raise ValueError("catalog source site URLs are invalid")
    if any(not isinstance(value, str) or not urlsplit(value).netloc for value in raw.values()):
        raise ValueError("catalog source site URLs are invalid")
    return {WebArenaSite(key): value for key, value in raw.items()}


def _load_inputs(catalog_path: Path, suite_path: Path, site_map_path: Path):
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    sites = load_remote_sites(site_map_path)
    suite = load_suite(suite_path)
    tasks = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    rows = catalog.get("rows") or []
    ids = [row.get("task_id") for row in rows]
    if (
        catalog.get("schema") != SCHEMA
        or ids != list(range(812))
        or catalog.get("task_ids") != ids
        or catalog.get("site_map_sha256") != _hash(site_map_path)
        or catalog.get("portable_suite_sha256") != _hash(suite_path)
    ):
        raise ValueError("full replay catalog binding is invalid")
    portable_ids = {row["task_id"] for row in rows if row.get("mode") == "portable"}
    if portable_ids != set(tasks):
        raise ValueError("portable suite differs from the catalog")
    source_sites = _source_sites(catalog)
    for row in rows:
        task_id = row["task_id"]
        mode = row.get("mode")
        if mode == "portable":
            source_actions = list(tasks[task_id].tool_calls)
        elif mode == "direct":
            source_actions = row.get("source_actions")
            if not isinstance(source_actions, list) or not source_actions:
                raise ValueError(f"task {task_id}: direct source actions are missing")
            contract = row.get("retrieval_contract")
            if contract is not None:
                validate_contract(contract, source_actions)
        else:
            raise ValueError(f"task {task_id}: unknown catalog mode")
        actions = rebind_remote_calls(
            source_actions, sites, set(TRUSTED_RECORDED_HOSTS), source_sites
        )
        if _json_sha256(actions) != row.get("bound_calls_sha256"):
            raise ValueError(f"task {task_id}: bound actions changed")
        if "recorded_response" in row:
            raise ValueError("recorded answers cannot enter a replay catalog")
    return catalog, sites, tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--site-reset-receipt", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--backend", choices=("browsertool", "playwright"), default="browsertool")
    parser.add_argument("--cowagent", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action-timeout-ms", type=int, default=30_000)
    parser.add_argument("--task-from", type=int, default=0)
    parser.add_argument("--task-to", type=int, default=811)
    args = parser.parse_args()
    if args.action_timeout_ms < 1:
        raise ValueError("action timeout must be positive")
    if args.backend == "browsertool":
        if args.cowagent is None or not args.cowagent.is_dir():
            raise ValueError("BrowserTool backend requires --cowagent repository")
        if str(args.cowagent) not in sys.path:
            sys.path.insert(0, str(args.cowagent))
    catalog, sites, portable_tasks = _load_inputs(
        args.catalog, args.portable_suite, args.site_map
    )
    source_sites = _source_sites(catalog)
    benchmark = remote_benchmark(sites)
    origins = {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
    ids = _task_ids(args.task_from, args.task_to)
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    binding = {
        "schema": "lex_webarena_full_replay_run/1",
        "catalog_sha256": _hash(args.catalog),
        "portable_suite_sha256": _hash(args.portable_suite),
        "site_map_sha256": _hash(args.site_map),
        "site_reset_receipt_sha256": _hash(args.site_reset_receipt),
        "binary_sha256": _hash(args.binary),
        "backend": args.backend,
        "runner_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
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
    }
    binding_path = args.output / "inputs.json"
    if binding_path.exists():
        if json.loads(binding_path.read_text(encoding="utf-8")) != binding:
            raise ValueError("resumed run inputs changed")
    else:
        binding_path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    progress = args.output / "progress.jsonl"
    done = _completed(progress, ids)
    pending = [
        row for row in catalog["rows"] if row["task_id"] in ids and row["task_id"] not in done
    ]
    if not pending:
        return
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    browser_args = argparse.Namespace(engine="chromium", binary=args.binary)
    with tempfile.TemporaryDirectory(prefix="webarena-full-catalog-") as profile:
        process = subprocess.Popen(
            _browser_command(browser_args, port, profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_cdp(endpoint, process)
            corrections = load_corrections()
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
                        source_actions, sites, set(TRUSTED_RECORDED_HOSTS), source_sites
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
                            portable_tasks[task_id]
                            if frozen["mode"] == "portable"
                            else None
                        ),
                        portable_action_timeout_ms=args.action_timeout_ms,
                        backend=args.backend,
                    )
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
