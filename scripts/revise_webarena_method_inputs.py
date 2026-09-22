"""Freeze explicit trajectory repairs without modifying predecessor evidence."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
)
from harness.trajectory_replay.retrieval_acceptance import digest, validate_contract


def revise(prior: dict, revisions: dict, sites: dict) -> dict:
    result = copy.deepcopy(prior)
    rows = {row["task_id"]: row for row in result["tasks"]}
    if not set(map(int, revisions)) <= set(rows):
        raise ValueError("revision is outside the frozen task set")
    for key, revision in revisions.items():
        row = rows[int(key)]
        if (
            not revision.get("cause")
            or revision.get("prior_calls_sha256") != row["source_calls_sha256"]
        ):
            raise ValueError("revision lacks a cause or names different predecessor actions")
        path = Path(revision["candidate"])
        if path.is_symlink() or not path.is_file():
            raise ValueError("candidate is missing or symlinked")
        candidate = json.loads(path.read_text())
        if candidate["task_id"] != row["task_id"]:
            raise ValueError("candidate task mismatch")
        calls = candidate["tool_calls"]
        if "retrieval_contract" in row:
            validate_contract(candidate["retrieval_contract"], calls)
            row["retrieval_contract"] = candidate["retrieval_contract"]
            row["retrieval_contract_source_sha256"] = file_sha256(path)
        elif "retrieval_contract" in candidate:
            raise ValueError("revision changes task type")
        if row.get("readiness") and len(calls) != len(row["source_actions"]):
            raise ValueError("step-count revision requires explicit readiness migration")
        row["source_actions"] = calls
        row["source_calls_sha256"] = digest(calls)
        row["revision"] = {**revision, "candidate_sha256": file_sha256(path)}
    for row in rows.values():
        row["actions"] = rebind_remote_calls(
            row["source_actions"], sites, {"127.0.0.1", "192.168.5.2", "host.docker.internal"}
        )
        row["bound_calls_sha256"] = digest(row["actions"])
    result["task_ids"] = sorted(rows)
    result["tasks"] = [rows[n] for n in result["task_ids"]]
    result["execution_order"] = "ascending_task_id"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prior", "revisions", "site-map", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output exists; frozen inputs are append-only")
    prior = json.loads(args.prior.read_text())
    if prior["site_map_sha256"] != file_sha256(args.site_map):
        raise ValueError("site map changed")
    result = revise(prior, json.loads(args.revisions.read_text()), load_remote_sites(args.site_map))
    result["predecessor_manifest_sha256"] = file_sha256(args.prior)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"task_count": len(result["tasks"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
