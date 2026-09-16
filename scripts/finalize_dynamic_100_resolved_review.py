#!/usr/bin/env python3
"""Bind the resolved 100/100 independent review to the final evidence pair."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


EXPECTED_DATASET = "ce24ba9bc36b83868b65b0fde0d8282d51319989a47925241ae9cb1d6016f390"
EXPECTED_RESULTS = "3b8636b41c26ef27ddb4b5e80cdb10ab953146da5325cddc4b5615ad40c11348"
EXPECTED_AUDIT = "70176db27735e53f003330de13b86199e750dd53236acf2f687fe841386630ec"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def checked(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    if digest(raw) != expected:
        raise ValueError(f"hash mismatch for {path}")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    dataset_raw = checked(args.evidence / "dataset.json", EXPECTED_DATASET)
    results_raw = checked(args.evidence / "results.jsonl", EXPECTED_RESULTS)
    audit_raw = checked(args.audit, EXPECTED_AUDIT)
    audit = json.loads(audit_raw)
    results = {row["attempt_id"]: row for row in (
        json.loads(line) for line in results_raw.splitlines() if line.strip())}
    if (audit.get("status") != "complete" or audit.get("reviewed_cases") != 100
            or audit.get("summary", {}).get("unresolved_cases") != []
            or audit.get("summary", {}).get("relations") != {
                "正确失败": 48, "正确通过": 52}):
        raise ValueError("resolved audit is not a complete 52/48 review")

    attempts = []
    classifications = Counter()
    for case in audit["cases"]:
        judgment = case["judgment"]
        if judgment["relation"] not in {"正确通过", "正确失败"}:
            raise ValueError("resolved audit contains a machine/reviewer disagreement")
        attempt_id = case["call_chain"]["attempt_id"]
        record = results.get(attempt_id)
        if record is None:
            raise ValueError("reviewed attempt is absent from final results: " + attempt_id)
        verdict = judgment["reported"]
        if verdict != judgment["independent"]:
            raise ValueError("independent and final verdict differ: " + attempt_id)
        if verdict == "pass":
            classification = "verified_contract_pass"
        elif not case["call_chain"]["request_ok"]:
            classification = "provider_access_or_extraction_failure"
        else:
            classification = "target_retrieved_but_frozen_contract_not_met"
        classifications[classification] += 1
        attempts.append({
            "attempt_id": attempt_id,
            "machine_verdict": verdict,
            "agent_verdict": verdict,
            "agent_evidence": judgment["reason"],
            "final_verdict": verdict,
            "adjudication": case["judgment"]["final_adjudication"],
            "classification": classification,
        })
    if len(attempts) != 100 or len({row["attempt_id"] for row in attempts}) != 100:
        raise ValueError("resolved attempt review is incomplete")

    review = {
        "schema_version": 1,
        "bindings": {
            "dataset_sha256": digest(dataset_raw),
            "results_sha256": digest(results_raw),
            "formal_audit_sha256": digest(audit_raw),
        },
        "reviewer": {
            "agent": "Codex independent full-scope benchmark audit",
            "model": "GPT-5",
            "effort": "high",
        },
        "review_protocol": {
            "protocol": "research/dynamic-100-formal-audit-plan.md",
            "scope": "all",
            "machine_judgments_withheld": True,
            "machine_judgments_withheld_during_initial_inspection": True,
            "complete_artifact_paths_supplied": True,
            "declared_acceptance_evidence_supplied": True,
            "policy_decisions_applied_after_separate_user_approval": True,
            "replacement_case_independently_qualified": True,
            "replacement_authoritative_call_used_original_legacy_interface": True,
            "superseded_replacement_calls_preserved": 2,
        },
        "total_attempts": 100,
        "expected_attempts": 100,
        "reviewed_attempts": 100,
        "accepted_unreviewed_passes": 0,
        "disagreements": 0,
        "unresolved": 0,
        "attempts": attempts,
        "review_rounds": [{
            "date": "2026-09-16",
            "title": "Dynamic 100 formal independent audit and resolution",
            "scope": "All 100 authoritative Moli WebFetch logical results, including the one-for-one Blueprint replacement.",
            "finding": "52 results satisfy their declared contracts and 48 do not; all 100 final machine verdicts agree with independent review.",
            "fix": "Deterministic reference/scoring defects were corrected offline; React/MUI policy was approved separately; Steam and two non-authoritative Blueprint calls were preserved as superseded evidence, while the final Blueprint result uses the original legacy interface with a verified postcheck.",
            "pending": "No unresolved cases. The public report was not changed or published in this task.",
        }],
    }
    review_path = args.evidence / "result-review.json"
    if review_path.exists():
        raise ValueError("final result-review.json already exists")
    review_path.write_bytes(json_bytes(review))

    old_audit = args.evidence / "result-audit.json"
    if old_audit.exists():
        old_raw = old_audit.read_bytes()
        history = args.evidence / "review-history"
        history.mkdir(exist_ok=True)
        archive = history / f"result-audit-pre-review-{digest(old_raw)[:16]}.json"
        if archive.exists() and archive.read_bytes() != old_raw:
            raise ValueError("pre-review audit archive collision")
        archive.write_bytes(old_raw)
    result_audit = {
        "schema_version": 1,
        "attempts": 100,
        "unique_attempts": 100,
        "unique_cases": 100,
        "unique_urls": 100,
        "providers": ["moli-webfetch"],
        "reviewed": 100,
        "disagreements": 0,
        "unresolved": 0,
        "task_pass": 52,
        "task_fail": 48,
        "classifications": dict(classifications),
        "provider_calls_added_for_replacement": 3,
        "physical_calls_in_lineage": 103,
        "authoritative_logical_results": 100,
    }
    old_audit.write_bytes(json_bytes(result_audit))

    lineage_path = args.evidence / "measurement-lineage.json"
    lineage = json.loads(lineage_path.read_text())
    lineage["independent_review"] = {
        "status": "complete",
        "reviewed": 100,
        "unresolved": 0,
        "result_review_sha256": digest(review_path.read_bytes()),
        "result_audit_sha256": digest(old_audit.read_bytes()),
        "formal_audit_sha256": digest(audit_raw),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    lineage_path.write_bytes(json_bytes(lineage))
    print(json.dumps({
        "result_review_sha256": digest(review_path.read_bytes()),
        "result_audit_sha256": digest(old_audit.read_bytes()),
        "reviewed": 100,
        "unresolved": 0,
        "task_pass": 52,
        "task_fail": 48,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
