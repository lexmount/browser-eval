"""Freeze one method's complete selected-task remote replay input before execution.

This intentionally fails until every RETRIEVE task has a current-run extraction
contract. A saved evaluator answer is never an acceptable substitute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.retrieval_acceptance import validate_contract

TRUSTED_SOURCE_HOSTS = {"127.0.0.1", "192.168.5.2", "host.docker.internal"}


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlinked frozen input: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_calls(task_id: int, canonical: dict, affected: dict | None) -> list[dict]:
    if affected is None:
        return canonical["tool_calls"]
    revision = affected.get("source_revision") or {}
    if revision:
        path = Path(revision["path"])
        if _sha256(path) != revision["sha256"]:
            raise ValueError(f"task {task_id}: revised source actions changed")
        calls = _read(path)["tool_calls"]
    elif canonical["evaluation_input"]["task_type"] == "RETRIEVE":
        path = (
            Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")
            / f"task-{task_id}.json"
        )
        calls = _read(path)["tool_calls"]
    else:
        # Non-retrieval acceptance is evaluated from current site state. Its
        # already-bound candidate actions are the execution authority here.
        calls = affected["actions"]
    # A source candidate may spell the old local gateway differently from the
    # diagnostic trace. Both must bind to the same frozen remote actions below.
    return calls


def freeze(
    *,
    dataset_path: Path,
    selection_path: Path,
    affected36_path: Path,
    site_map_path: Path,
    binary_path: Path,
    method_id: str,
    engine: str,
    retrieval_catalog: dict[int, Path] | None = None,
) -> dict[str, Any]:
    if engine not in {"chromium", "moli"} or not method_id or "/" in method_id:
        raise ValueError("invalid method identity")
    dataset = _read(dataset_path)
    selection = _read(selection_path)
    affected = _read(affected36_path)
    if (
        dataset.get("schema") != "lex_webarena_verified_trajectory_dataset/1"
        or affected.get("schema") != "original36_affected_cohort_inputs/1"
        or affected.get("promotable") is not True
    ):
        raise ValueError("selected-task dataset or candidate manifest is not frozen")
    canonical = dataset.get("trajectories") or []
    affected_rows = affected.get("candidates") or []
    by_affected = {int(row["task_id"]): row for row in affected_rows}
    by_canonical = {int(row["task_id"]): row for row in canonical}
    task_ids = sorted(int(row["task_id"]) for row in selection.get("tasks") or [])
    if (
        selection.get("schema") != "lex_webarena_verified_pilot/1"
        or len(canonical) != len(task_ids)
        or len(by_canonical) != len(task_ids)
        or not task_ids
        or len(set(task_ids)) != len(task_ids)
        or set(by_canonical) != set(task_ids)
        or len(affected_rows) != 36
        or len(by_affected) != 36
        or set(by_affected) != set(affected["candidate_task_ids"])
        or _sha256(site_map_path) != affected["site_map_sha256"]
    ):
        raise ValueError("task coverage or remote site map changed")
    sites = load_remote_sites(site_map_path)
    retrieval_catalog = retrieval_catalog or {}
    rows = []
    missing_contracts = []
    for task_id in task_ids:
        task = by_canonical[task_id]
        changed = by_affected.get(task_id)
        source_actions = _source_calls(task_id, task, changed)
        candidate_path = None
        if task["evaluation_input"]["task_type"] == "RETRIEVE":
            candidate_path = retrieval_catalog.get(task_id)
            if candidate_path is None and changed is not None:
                revision = changed.get("source_revision") or {}
                if "retrieval-candidates/" in str(revision.get("path") or ""):
                    candidate_path = Path(revision["path"])
            if candidate_path is None:
                candidate_path = (
                    Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")
                    / f"task-{task_id}.json"
                )
            if changed is None and candidate_path.is_file():
                source_actions = _read(candidate_path)["tool_calls"]
        actions = rebind_remote_calls(source_actions, sites, TRUSTED_SOURCE_HOSTS)
        if changed is not None and (
            _digest(actions) != changed["bound_calls_sha256"] or actions != changed["actions"]
        ):
            raise ValueError(f"task {task_id}: qualified candidate remote actions changed")
        row = {
            "task_id": task_id,
            "task_contract_sha256": _digest(
                {"task": task["task"], "evaluation_input": task["evaluation_input"]}
            ),
            "source_actions": source_actions,
            "source_calls_sha256": _digest(source_actions),
            "actions": actions,
            "bound_calls_sha256": _digest(actions),
            "readiness": changed["readiness"] if changed is not None else [],
        }
        if changed is not None:
            row["prior_source_calls_sha256"] = changed["source_calls_sha256"]
        if task["evaluation_input"]["task_type"] == "RETRIEVE":
            assert candidate_path is not None
            if not candidate_path.is_file():
                missing_contracts.append(task_id)
            else:
                candidate = _read(candidate_path)
                if (
                    candidate.get("task_id") != task_id
                    or candidate.get("tool_calls") != source_actions
                ):
                    raise ValueError(f"task {task_id}: retrieval contract uses other actions")
                contract = candidate.get("retrieval_contract")
                if contract is None:
                    missing_contracts.append(task_id)
                else:
                    validate_contract(contract, source_actions)
                    row["retrieval_contract"] = contract
                    row["retrieval_contract_source_sha256"] = _sha256(candidate_path)
        rows.append(row)
    if missing_contracts:
        raise ValueError(f"current-run retrieval contracts are missing: {missing_contracts}")
    if set(retrieval_catalog) - set(task_ids):
        raise ValueError("retrieval catalog contains an out-of-scope task")
    return {
        "schema": "webarena_three_method_inputs/1",
        "promotable": True,
        "method_id": method_id,
        "engine": engine,
        "browser_binary_sha256": _sha256(binary_path),
        "site_map_sha256": _sha256(site_map_path),
        "dataset_sha256": _sha256(dataset_path),
        "selection_sha256": _sha256(selection_path),
        "candidate36_manifest_sha256": _sha256(affected36_path),
        "task_ids": task_ids,
        "tasks": rows,
        "site_readiness": affected.get("site_readiness") or [],
        "run_count": 1,
        "retry_count": 0,
        "site_reset_between_tasks": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--affected36", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--method-id", required=True)
    parser.add_argument("--engine", choices=("chromium", "moli"), required=True)
    parser.add_argument("--retrieval-catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = _read(args.retrieval_catalog) if args.retrieval_catalog else {}
    catalog_paths = {int(key): Path(value) for key, value in catalog.items()}
    frozen = freeze(
        dataset_path=args.dataset,
        selection_path=args.selection,
        affected36_path=args.affected36,
        site_map_path=args.site_map,
        binary_path=args.binary,
        method_id=args.method_id,
        engine=args.engine,
        retrieval_catalog=catalog_paths,
    )
    if args.output.exists():
        raise ValueError("frozen manifest output exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"task_count": len(frozen["tasks"]), "sha256": _sha256(args.output)}))


if __name__ == "__main__":
    main()
