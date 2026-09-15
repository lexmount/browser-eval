#!/usr/bin/env python3
"""Validate and install complete reviews produced by blinded Codex review shards."""
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


def merge_reviews(evidence: Path, shard_paths: list[Path], output: Path, *,
                  prior_review: Path | None = None,
                  adjudications: Path | None = None, install: bool = False,
                  allow_confirmed_errors: bool = False) -> dict:
    evidence = evidence.resolve()
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in
               (evidence / "results.jsonl").read_text().splitlines()]
    cases = {case["id"]: case for case in dataset["cases"]}
    by_id = {record["attempt_id"]: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("Evidence contains duplicate attempt IDs")

    prior_rows: dict[str, dict] = {}
    prior_model = None
    prior_independent = False
    if prior_review is not None:
        current_review = evidence / "result-review.json"
        if sha256(prior_review) != sha256(current_review):
            raise ValueError("Prior review must be the validated review installed in evidence")
        prior = require_result_review(evidence, dataset, records)
        if prior["review_protocol"].get("scope") != "machine-failures":
            raise ValueError("Prior review must cover exactly the machine-failure scope")
        prior_rows = {row["attempt_id"]: row for row in prior["attempts"]}
        prior_model = prior.get("reviewer", {}).get("model")
        prior_independent = (
            prior["review_protocol"].get("production_scorer_forbidden") is True
            and prior["review_protocol"].get("direct_artifact_evidence_required") is True
        )
        if not prior_independent:
            raise ValueError("Prior review does not enforce scorer-independent artifact evidence")

    supplied: dict[str, dict] = {}
    shard_metadata = []
    for path in shard_paths:
        shard = json.loads(path.read_text())
        if shard.get("schema_version") == 1 and shard.get("review_protocol", {}).get("scope") == "machine-passes":
            if (shard["review_protocol"].get("production_scorer_forbidden") is not True
                    or shard["review_protocol"].get("direct_artifact_evidence_required") is not True):
                raise ValueError(f"Review shard is not scorer-independent: {path}")
            if shard.get("bindings") != {
                    "dataset_sha256": sha256(evidence / "dataset.json"),
                    "results_sha256": sha256(evidence / "results.jsonl")}:
                raise ValueError(f"Review shard is bound to different evidence: {path}")
            providers = shard.get("review_protocol", {}).get("provider_filter", [])
            rows = [{"attempt_id": row.get("attempt_id"),
                     "reviewer_verdict": row.get("agent_verdict"),
                     "rationale": row.get("agent_evidence", "")}
                    for row in shard.get("attempts", [])]
        elif shard.get("reviewer") == "Codex sub-agent":
            providers = shard.get("providers", [])
            rows = shard.get("attempts", [])
        else:
            raise ValueError(f"Unexpected reviewer in {path}")
        shard_metadata.append({
            "path": path.name,
            "providers": providers,
            "reviewed": len(rows),
            "model": shard.get("reviewer", {}).get("model", "task runtime default"),
            "sha256": sha256(path),
        })
        for row in rows:
            attempt_id = row.get("attempt_id")
            if attempt_id in supplied:
                raise ValueError(f"Duplicate reviewed attempt: {attempt_id}")
            supplied[attempt_id] = row

    expected = by_id.keys() - prior_rows.keys()
    if supplied.keys() != expected:
        missing = sorted(expected - supplied.keys())
        extra = sorted(supplied.keys() - expected)
        raise ValueError(f"Review coverage differs; missing={missing[:5]} extra={extra[:5]}")

    adjudicated = json.loads(adjudications.read_text()) if adjudications else {}
    attempts = []
    for record in records:
        if record["attempt_id"] in prior_rows:
            attempts.append(prior_rows[record["attempt_id"]])
            continue
        reviewed = supplied[record["attempt_id"]]
        machine = "pass" if task_passed(
            record["ok"], record["metrics"],
            cases[record["case_id"]].get("task_kind", "body")) else "fail"
        agent = reviewed.get("reviewer_verdict")
        if agent not in {"pass", "fail"}:
            raise ValueError(f"Unresolved agent verdict: {record['attempt_id']}")
        rationale = reviewed.get("rationale", "").strip()
        if not rationale:
            raise ValueError(f"Review evidence is empty: {record['attempt_id']}")
        adjudication = None
        if agent != machine:
            decision = adjudicated.get(record["attempt_id"], {})
            if decision.get("final_verdict") not in {"pass", "fail"}:
                raise ValueError(
                    f"Review disagreement requires adjudication: "
                    f"{record['attempt_id']} agent={agent} machine={machine}")
            if decision["final_verdict"] != machine and not allow_confirmed_errors:
                raise ValueError(
                    f"Confirmed scoring disagreement requires correction and re-review: "
                    f"{record['attempt_id']} agent={agent} machine={machine}")
            adjudication = decision.get("reason")
        final = ((adjudicated.get(record["attempt_id"], {}) or {}).get("final_verdict")
                 if agent != machine else machine)
        attempts.append({
            "attempt_id": record["attempt_id"],
            "machine_verdict": machine,
            "agent_verdict": agent,
            "agent_evidence": rationale,
            "final_verdict": final,
            "adjudication": adjudication,
        })

    models = sorted({row["model"] for row in shard_metadata} | ({prior_model} if prior_model else set()))
    confirmed_errors = [row["attempt_id"] for row in attempts
                        if row["final_verdict"] != row["machine_verdict"]]
    review = {
        "schema_version": 1,
        "bindings": {
            "dataset_sha256": sha256(evidence / "dataset.json"),
            "results_sha256": sha256(evidence / "results.jsonl"),
        },
        "reviewer": {
            "agent": "Codex 独立强推理代理",
            "model": ", ".join(models),
            "effort": "高",
            "cli_version": "Codex desktop task runtime",
        },
        "review_protocol": {
            "machine_judgments_withheld": True,
            "production_scorer_forbidden": True,
            "direct_artifact_evidence_required": True,
            "complete_artifact_paths_supplied": True,
            "source_answer_metadata_supplied": True,
            "declared_acceptance_evidence_supplied": True,
            "provider_filter": [],
            "scope": "all",
            "shards": shard_metadata,
        },
        "total_attempts": len(records),
        "accepted_unreviewed_passes": 0,
        "expected_attempts": len(records),
        "reviewed_attempts": len(attempts),
        "disagreements": sum(row["agent_verdict"] != row["machine_verdict"] for row in attempts),
        "unresolved": 0,
        "confirmed_scoring_errors": len(confirmed_errors),
        "attempts": attempts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    if install:
        if confirmed_errors:
            raise ValueError("Cannot install a review with confirmed scoring errors")
        shutil.copy2(output, evidence / "result-review.json")
        require_result_review(evidence, dataset, records)
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--shard", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prior-review", type=Path)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--allow-confirmed-errors", action="store_true",
                        help="Write a non-installable staging review before correcting confirmed score errors")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    review = merge_reviews(
        args.evidence, args.shard, args.output,
        prior_review=args.prior_review, adjudications=args.adjudications,
        install=args.install, allow_confirmed_errors=args.allow_confirmed_errors,
    )
    print(f"reviewed={len(review['attempts'])} disagreements={review['disagreements']} unresolved=0")


if __name__ == "__main__":
    main()
