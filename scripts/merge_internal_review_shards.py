#!/usr/bin/env python3
"""Validate and install attempt-complete reviews produced by Codex task agents."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import task_passed
from harness.web_fetch.result_review import require_result_review


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--shard", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()

    evidence = args.evidence.resolve()
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in
               (evidence / "results.jsonl").read_text().splitlines()]
    cases = {case["id"]: case for case in dataset["cases"]}
    by_id = {record["attempt_id"]: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("Evidence contains duplicate attempt IDs")

    supplied: dict[str, dict] = {}
    shard_metadata = []
    for path in args.shard:
        shard = json.loads(path.read_text())
        if shard.get("reviewer") != "Codex sub-agent":
            raise ValueError(f"Unexpected reviewer in {path}")
        shard_metadata.append({
            "path": path.name,
            "providers": shard.get("providers", []),
            "reviewed": len(shard.get("attempts", [])),
            "sha256": sha256(path),
        })
        for row in shard.get("attempts", []):
            attempt_id = row.get("attempt_id")
            if attempt_id in supplied:
                raise ValueError(f"Duplicate reviewed attempt: {attempt_id}")
            supplied[attempt_id] = row

    if supplied.keys() != by_id.keys():
        missing = sorted(by_id.keys() - supplied.keys())
        extra = sorted(supplied.keys() - by_id.keys())
        raise ValueError(f"Review coverage differs; missing={missing[:5]} extra={extra[:5]}")

    attempts = []
    for record in records:
        reviewed = supplied[record["attempt_id"]]
        machine = "pass" if task_passed(
            record["ok"], record["metrics"],
            cases[record["case_id"]].get("task_kind", "body")) else "fail"
        agent = reviewed.get("reviewer_verdict")
        if agent not in {"pass", "fail"}:
            raise ValueError(f"Unresolved agent verdict: {record['attempt_id']}")
        if agent != machine:
            raise ValueError(
                f"Review disagreement requires correction before publication: "
                f"{record['attempt_id']} agent={agent} machine={machine}")
        attempts.append({
            "attempt_id": record["attempt_id"],
            "machine_verdict": machine,
            "agent_verdict": agent,
            "agent_evidence": reviewed.get("rationale", ""),
            "final_verdict": machine,
            "adjudication": None,
        })

    review = {
        "schema_version": 1,
        "bindings": {
            "dataset_sha256": sha256(evidence / "dataset.json"),
            "results_sha256": sha256(evidence / "results.jsonl"),
        },
        "reviewer": {
            "agent": "Codex 独立强推理代理",
            "model": "任务运行时默认模型",
            "effort": "高",
            "cli_version": "Codex desktop task runtime",
        },
        "review_protocol": {
            "machine_judgments_withheld": True,
            "complete_artifact_paths_supplied": True,
            "declared_acceptance_evidence_supplied": True,
            "provider_filter": [],
            "scope": "all",
            "shards": shard_metadata,
        },
        "total_attempts": len(records),
        "accepted_unreviewed_passes": 0,
        "expected_attempts": len(records),
        "reviewed_attempts": len(attempts),
        "disagreements": 0,
        "unresolved": 0,
        "attempts": attempts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    if args.install:
        shutil.copy2(args.output, evidence / "result-review.json")
        require_result_review(evidence, dataset, records)
    print(f"reviewed={len(attempts)} disagreements=0 unresolved=0")


if __name__ == "__main__":
    main()
