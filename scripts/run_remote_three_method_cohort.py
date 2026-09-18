"""Run one frozen selected-task WebArena replay method on the remote deployment.

Each method has its own immutable manifest, browser binary and evidence directory.
The four mutable sites are restored once before each browser method. A fresh
BrowserTool context is created for each task, without resetting site data between
tasks. Certification means that every
declared call and its evidence is complete; it does not mean the calls pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
    validate_remote_cohort_reset_receipt,
)
from harness.trajectory_replay.retrieval_acceptance import validate_contract
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_replay import _json_sha256

# Direct `python scripts/...py` execution starts with scripts/ on sys.path, while
# tests import this file as a module from the repository root.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.run_remote_candidate_cohort_diagnostic import (  # noqa: E402
    _browser_command,
    _run_task,
    _verify_result_evidence,
    _wait_cdp,
    _write,
)

ROOT = REPOSITORY_ROOT
TASK_SELECTION = ROOT / "evaluation/trajectory-replay/webarena-verified/selection.json"
RESET_SCOPE = "once_before_three_method_cohort"
SOURCE_HOSTS = {"127.0.0.1", "192.168.5.2", "host.docker.internal"}
METHOD_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,79}\Z")


def _checked_file(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or symlinked")
    return file_sha256(path)


def _task_ids() -> list[int]:
    selection = json.loads(TASK_SELECTION.read_text(encoding="utf-8"))
    if selection.get("schema") != "lex_webarena_verified_pilot/1":
        raise ValueError("selected-task schema changed")
    ids = [row["task_id"] for row in selection["tasks"]]
    if not ids or len(set(ids)) != len(ids) or any(type(value) is not int for value in ids):
        raise ValueError("selected-task set is empty or duplicated")
    return ids


def _preflight(args: argparse.Namespace) -> tuple[dict, dict, object, dict[int, dict]]:
    for label, path in (
        ("manifest", args.manifest),
        ("dataset", args.dataset),
        ("browser binary", args.binary),
        ("site map", args.site_map),
        ("cohort reset hook", args.cohort_reset_hook),
    ):
        _checked_file(path, label)
    if args.output.exists():
        raise ValueError("output exists; evidence is append-only")
    if not METHOD_ID.fullmatch(args.method_id):
        raise ValueError("method ID is not a safe stable identifier")
    if args.engine not in {"moli", "chromium"}:
        raise ValueError("unsupported browser engine")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "webarena_three_method_inputs/1"
        or manifest.get("method_id") != args.method_id
        or manifest.get("engine") != args.engine
        or manifest.get("browser_binary_sha256") != file_sha256(args.binary)
        or manifest.get("dataset_sha256") != file_sha256(args.dataset)
        or manifest.get("selection_sha256") != file_sha256(TASK_SELECTION)
        or manifest.get("site_map_sha256") != file_sha256(args.site_map)
        or manifest.get("run_count") != 1
        or manifest.get("retry_count") != 0
        or manifest.get("site_reset_between_tasks") is not False
        or manifest.get("promotable") is not True
    ):
        raise ValueError("frozen method, binary, site map or run policy changed")
    for name in ("browser_tool.py", "browser_service.py"):
        _checked_file(args.cowagent / "agent/tools/browser" / name, f"CowAgent {name}")
    sites = load_remote_sites(args.site_map)
    expected_ids = _task_ids()
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
            or rebind_remote_calls(source, sites, SOURCE_HOSTS) != actions
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


def _reference_hashes(args: argparse.Namespace) -> dict[str, str]:
    paths = {
        "manifest": args.manifest,
        "browser_binary": args.binary,
        "site_map": args.site_map,
        "cohort_reset_hook": args.cohort_reset_hook,
        "cowagent_browser_tool": args.cowagent / "agent/tools/browser/browser_tool.py",
        "cowagent_browser_service": args.cowagent / "agent/tools/browser/browser_service.py",
        "task_selection": TASK_SELECTION,
        "frozen_trajectories": args.dataset,
        "task_corrections": ROOT
        / "evaluation/trajectory-replay/webarena-verified/task-corrections.json",
        "runner": Path(__file__).resolve(),
        "candidate_runner": ROOT / "scripts/run_remote_candidate_cohort_diagnostic.py",
        "replay_logic": ROOT / "harness/trajectory_replay/webarena_replay.py",
        "retrieval_acceptance": ROOT / "harness/trajectory_replay/retrieval_acceptance.py",
        "remote_runtime": ROOT / "harness/trajectory_replay/remote_runtime.py",
    }
    return {name: _checked_file(path, name) for name, path in paths.items()}


def _certificate(
    *,
    manifest: dict,
    rows: list[dict],
    before: dict[str, str],
    after: dict[str, str],
    evidence_valid: bool,
) -> bool:
    ids = manifest["task_ids"]
    return (
        len(rows) == len(ids)
        and [row["task_id"] for row in rows] == ids
        and len({row["task_id"] for row in rows}) == len(ids)
        and all(
            row["executed_actions_sha256"] == task["bound_calls_sha256"]
            for row, task in zip(rows, manifest["tasks"], strict=True)
        )
        and before == after
        and evidence_valid
    )


def _verify_complete_evidence(output: Path, rows: list[dict], tasks: dict[int, dict]) -> None:
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


def _reset_method(args: argparse.Namespace, manifest: dict) -> str:
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
    validated = validate_remote_cohort_reset_receipt(
        receipt_path,
        site_map_sha256=manifest["site_map_sha256"],
        app_id=args.app_id,
        reset_scope=RESET_SCOPE,
        site_reset_between_tasks=False,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    args = parser.parse_args(argv)
    manifest, sites, benchmark, tasks = _preflight(args)
    if str(args.cowagent) not in sys.path:
        sys.path.insert(0, str(args.cowagent))
    os.umask(0o077)
    args.output.mkdir(parents=True, mode=0o700)
    reset_sha256 = _reset_method(args, manifest)
    # The cohort's reference boundary starts after its one site restoration.
    # Comparing pre-reset bytes with post-run bytes would reject a valid reset.
    before = _reference_hashes(args)
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
            for task_id in manifest["task_ids"]:
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
    after = _reference_hashes(args)
    _verify_complete_evidence(args.output, results, tasks)
    certified = _certificate(
        manifest=manifest, rows=results, before=before, after=after, evidence_valid=True
    )
    receipt = {
        "schema": "webarena_three_method_cohort/1",
        "method_id": args.method_id,
        "engine": args.engine,
        "promotable": certified,
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


if __name__ == "__main__":
    raise SystemExit(main())
