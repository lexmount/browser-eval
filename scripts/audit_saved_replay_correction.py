"""Compare two correction catalogs on immutable saved replay evidence, without browser calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.remote_runtime import file_sha256, load_remote_sites, remote_benchmark
from harness.trajectory_replay.webarena_corrections import apply_correction, evaluate_corrected_contract, load_corrections


def audit_saved_correction(*, task_dir: Path, task_id: int, site_map: Path,
                           before_catalog: Path, after_catalog: Path) -> dict:
    receipt_path = task_dir.parent / "receipt.json"
    sources = [receipt_path, site_map, before_catalog, after_catalog]
    if any(not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)) for path in sources):
        raise ValueError("correction audit inputs must be regular, non-symlinked files")
    receipt = json.loads(receipt_path.read_text())
    if receipt["site_map_sha256"] != file_sha256(site_map):
        raise ValueError("site map differs from saved execution")
    rows = [row for row in receipt["results"] if row["task_id"] == task_id]
    if len(rows) != 1:
        raise ValueError("expected exactly one saved task result")
    row = rows[0]
    hashes = {}
    names = [("network_har", "network.har"), ("trace", "trace.json")]
    completed = row["completed_actions"] == row["action_count"]
    if completed:
        names += [("evaluation", "evaluation.json"), ("actual_response", "actual_response.json")]
    elif row["status"] != "fail" or row.get("score") is not None:
        raise ValueError("incomplete execution has inconsistent verdict")
    for key, name in names:
        path = task_dir / name
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("saved evidence is missing or symlinked")
        hashes[key] = file_sha256(path)
        if hashes[key] != row["evidence_sha256"][key]:
            raise ValueError("saved evidence differs from execution receipt")
    if not completed:
        return {"schema": "lex_saved_replay_correction_audit/1", "task_id": task_id,
                "method_id": receipt["method_id"], "browser_calls": 0,
                "source_receipt_sha256": file_sha256(receipt_path), "evidence_sha256": hashes,
                "site_map_sha256": file_sha256(site_map), "saved_execution_status": "fail",
                "evaluation_reached": False, "completed_actions": row["completed_actions"],
                "action_count": row["action_count"], "score_changed": False,
                "before_catalog_sha256": file_sha256(before_catalog),
                "after_catalog_sha256": file_sha256(after_catalog)}
    benchmark = remote_benchmark(load_remote_sites(site_map))
    response = json.loads((task_dir / "actual_response.json").read_text())
    evaluations = []
    for source in (before_catalog, after_catalog):
        catalog = load_corrections(source)
        contract = apply_correction(benchmark.get_task(task_id), catalog)
        result = evaluate_corrected_contract(benchmark, contract, agent_response=response,
                                             network_trace=task_dir / "network.har")
        evaluations.append({"catalog_sha256": catalog.source_sha256,
                            "record_sha256": catalog.record_sha256s[task_id],
                            "correction_id": contract.correction_id,
                            "task_sha256": contract.corrected_task_sha256,
                            "score": result.score, "evaluation": result.model_dump(mode="json")})
    saved = json.loads((task_dir / "evaluation.json").read_text())
    if evaluations[0]["score"] != saved["score"] or evaluations[0]["task_sha256"] != saved["corrected_task_sha256"]:
        raise ValueError("predecessor does not reproduce the saved scoring contract and score")
    return {"schema": "lex_saved_replay_correction_audit/1", "task_id": task_id,
            "method_id": receipt["method_id"], "browser_calls": 0,
            "source_receipt_sha256": file_sha256(receipt_path), "evidence_sha256": hashes,
            "site_map_sha256": file_sha256(site_map), "saved_execution_status": row["status"],
            "before": evaluations[0], "after": evaluations[1],
            "score_changed": evaluations[0]["score"] != evaluations[1]["score"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--before-catalog", type=Path, required=True)
    parser.add_argument("--after-catalog", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit_saved_correction(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
