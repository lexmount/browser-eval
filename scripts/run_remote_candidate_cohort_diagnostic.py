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
from pathlib import Path
from urllib.parse import urlsplit

from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.remote_execution import (
    _browser_command,
    _run_task,
    _verify_result_evidence,
    _wait_cdp,
    _write,
)

from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
    validate_remote_cohort_reset_receipt,
)
from harness.trajectory_replay.webarena_corrections import (
    load_corrections,
)
from harness.trajectory_replay.webarena_replay import (
    _json_sha256,
)




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
        "remote_execution": root / "harness/trajectory_replay/remote_execution.py",
        "replay_logic": root / "harness/trajectory_replay/webarena_replay.py",
        "retrieval_acceptance": root / "harness/trajectory_replay/retrieval_acceptance.py",
        "remote_runtime": root / "harness/trajectory_replay/remote_runtime.py",
    }
    for key, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"formal input is missing or symlinked: {key}")
    return {key: file_sha256(path) for key, path in paths.items()}
















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
