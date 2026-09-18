"""Supersede one affected-cohort input after a reviewed task/reference correction.

The prior manifest stays immutable. The replacement needs a current official
task, an independent reference audit, and paired successful live executions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.retrieval_acceptance import validate_contract


def sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlinked evidence: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> dict:
    sha(path)
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def revise(
    *, prior_path: Path, dataset_path: Path, site_map_path: Path,
    candidate_path: Path, audit_path: Path, chromium_rescore_path: Path,
    moli_rescore_path: Path,
) -> dict:
    prior = read(prior_path)
    dataset = read(dataset_path)
    candidate = read(candidate_path)
    audit = read(audit_path)
    site_sha = sha(site_map_path)
    if (
        prior.get("schema") != "original36_affected_cohort_inputs/1"
        or prior.get("promotable") is not True
        or prior.get("site_map_sha256") != site_sha
        or dataset.get("schema") != "lex_webarena_verified_trajectory_dataset/1"
        or len(prior.get("candidates", [])) != 36
        or len(set(prior.get("candidate_task_ids", []))) != 36
    ):
        raise ValueError("prior cohort or current dataset is not frozen")
    task_id = int(candidate["task_id"])
    task = next((x for x in dataset["trajectories"] if x["task_id"] == task_id), None)
    old = next((x for x in prior["candidates"] if x["task_id"] == task_id), None)
    if old is None or task is None or task_id not in prior["candidate_task_ids"]:
        raise ValueError("replacement task is outside the frozen affected cohort")
    if (
        audit.get("schema") != "trajectory_replay_independent_reference/1"
        or audit.get("task_id") != task_id
        or task["task"].get("revision", 0) <= 1
        or task["evaluation_input"]["task_type"] != "RETRIEVE"
        or task["evaluation_input"] != audit.get("expected_response")
        or candidate["retrieval_contract"]["task_id"] != task_id
        or not audit.get("regression_ids")
    ):
        raise ValueError("corrected task/reference audit does not match the official task")
    calls = candidate["tool_calls"]
    validate_contract(candidate["retrieval_contract"], calls)
    call_sha = digest(calls)
    sites = load_remote_sites(site_map_path)
    actions = rebind_remote_calls(
        calls, sites, {"127.0.0.1", "192.168.5.2", "host.docker.internal"}
    )
    proofs = {}
    official_identity = None
    for engine, path in (("chromium", chromium_rescore_path), ("moli", moli_rescore_path)):
        receipt = read(path)
        result_path = path.parent / ("result.json" if engine == "chromium" else "receipt.json")
        result = read(result_path)
        if (
            receipt.get("schema") != "webarena_saved_retrieval_rescore/1"
            or receipt.get("task_id") != task_id
            or receipt.get("score") != 1.0
            or receipt.get("browser_calls") != 0
            or receipt.get("site_map_sha256") != site_sha
            or receipt.get("source_result_sha256") != sha(result_path)
            or receipt.get("evaluation", {}).get("task_revision") != task["task"]["revision"]
            or receipt.get("evaluation", {}).get("status") != "success"
            or receipt.get("evaluation", {}).get("score") != 1.0
        ):
            raise ValueError(f"{engine} live qualification is not bound to this reference")
        identity = (
            receipt["dataset_sha256"],
            receipt["evaluation"]["webarena_verified_data_checksum"],
            receipt["evaluation"]["webarena_verified_evaluator_checksum"],
        )
        if official_identity is not None and identity != official_identity:
            raise ValueError("paired qualification used different official references")
        official_identity = identity
        measured = result if engine == "chromium" else next(
            (x for x in result["results"] if x["task_id"] == task_id), None
        )
        if (
            measured is None
            or measured.get("executed_actions_sha256") != call_sha
            or measured.get("action_count") != len(calls)
            or measured.get("score") != 1.0
        ):
            raise ValueError(f"{engine} did not complete the replacement actions")
        proofs[engine] = {"path": str(path), "sha256": sha(path)}
    output = deepcopy(prior)
    replacement = deepcopy(old)
    replacement.update({
        "source_calls_sha256": call_sha,
        "source_revision": {
            "path": str(candidate_path),
            "sha256": sha(candidate_path),
            "reason": audit["root_cause"],
            "action_count": len(calls),
            "independent_reference_audit_sha256": sha(audit_path),
            "paired_qualification": proofs,
            "superseded_source_revision_sha256": old["source_revision"]["sha256"],
        },
        "bound_calls_sha256": digest(actions),
        "actions": actions,
        "readiness": [],
    })
    output["candidates"] = [
        replacement if row["task_id"] == task_id else row for row in output["candidates"]
    ]
    output["supersedes_manifest_sha256"] = sha(prior_path)
    output["reference_corrections"] = [{
        "task_id": task_id,
        "dataset_sha256": sha(dataset_path),
        "audit_sha256": sha(audit_path),
        "candidate_sha256": sha(candidate_path),
        "paired_qualification": proofs,
    }]
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "prior", "dataset", "site-map", "candidate", "audit",
        "chromium-rescore", "moli-rescore", "output",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    output = revise(
        prior_path=args.prior, dataset_path=args.dataset, site_map_path=args.site_map,
        candidate_path=args.candidate, audit_path=args.audit,
        chromium_rescore_path=args.chromium_rescore, moli_rescore_path=args.moli_rescore,
    )
    if args.output.exists():
        raise ValueError("revised manifest already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"candidate_count": len(output["candidates"]), "sha256": sha(args.output)}))


if __name__ == "__main__":
    main()
