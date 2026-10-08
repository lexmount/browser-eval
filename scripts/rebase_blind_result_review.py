"""Rebind a completed blind review after score-only result changes.

The reviewer verdict remains independent because the machine verdict was hidden.
Rebasing is allowed only when every reviewed task and reviewer-visible evidence
binding is identical. Any remaining reviewer/scorer disagreement must be
explicitly adjudicated back to the corrected machine verdict before install.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.record_io import load_jsonl
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.result_review import require_result_review, review_input_identity


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


def rebase(current: Path, reviewed: Path, output: Path, adjudications: Path | None,
           *, review_file: Path | None = None, install: bool = False) -> dict:
    current_dataset = json.loads((current / "dataset.json").read_text())
    reviewed_dataset = json.loads((reviewed / "dataset.json").read_text())
    if current_dataset.get("cases") != reviewed_dataset.get("cases"):
        raise ValueError("Reviewed and current task definitions differ")
    current_rows = _rows(current / "results.jsonl")
    reviewed_rows = _rows(reviewed / "results.jsonl")
    current_by_id = {row["attempt_id"]: row for row in current_rows}
    reviewed_by_id = {row["attempt_id"]: row for row in reviewed_rows}
    if (len(current_by_id) != len(current_rows) or len(reviewed_by_id) != len(reviewed_rows)
            or current_by_id.keys() != reviewed_by_id.keys()):
        raise ValueError("Reviewed and current attempt inventories differ")

    review_path = review_file or (reviewed / "result-review.json")
    prior = json.loads(review_path.read_text())
    if prior.get("bindings") != {
            "dataset_sha256": _sha(reviewed / "dataset.json"),
            "results_sha256": _sha(reviewed / "results.jsonl")}:
        raise ValueError("Prior review is not bound to reviewed evidence")
    if prior.get("review_protocol", {}).get("machine_judgments_withheld") is not True:
        raise ValueError("Prior review was not blinded to machine judgments")
    prior_attempts = {row.get("attempt_id"): row for row in prior.get("attempts", [])}
    if len(prior_attempts) != len(prior.get("attempts", [])) or prior_attempts.keys() != current_by_id.keys():
        raise ValueError("Prior review does not cover every current attempt")
    for attempt_id, row in current_by_id.items():
        if review_input_identity(row) != review_input_identity(reviewed_by_id[attempt_id]):
            raise ValueError("Reviewer-visible evidence changed: " + attempt_id)

    decisions = json.loads(adjudications.read_text()) if adjudications else {}
    cases = {case["id"]: case for case in current_dataset["cases"]}
    attempts = []
    for row in current_rows:
        attempt_id = row["attempt_id"]
        old = prior_attempts[attempt_id]
        agent = old.get("agent_verdict")
        if agent not in {"pass", "fail"} or not str(old.get("agent_evidence", "")).strip():
            raise ValueError("Prior independent judgment is incomplete: " + attempt_id)
        machine = "pass" if task_passed(
            row["ok"], row["metrics"], cases[row["case_id"]].get("task_kind", "body")) else "fail"
        adjudication = None
        if agent != machine:
            decision = decisions.get(attempt_id, {})
            if decision.get("final_verdict") != machine or not str(decision.get("reason", "")).strip():
                raise ValueError("Current disagreement requires machine-consistent adjudication: " + attempt_id)
            adjudication = decision["reason"]
        attempts.append({
            "attempt_id": attempt_id,
            "machine_verdict": machine,
            "agent_verdict": agent,
            "agent_evidence": old["agent_evidence"],
            "reviewer_anomalies": old.get("reviewer_anomalies", []),
            "final_verdict": machine,
            "adjudication": adjudication,
        })

    result = json.loads(json.dumps(prior))
    result["bindings"] = {
        "dataset_sha256": _sha(current / "dataset.json"),
        "results_sha256": _sha(current / "results.jsonl"),
    }
    result["attempts"] = attempts
    result.setdefault("review_protocol", {})["score_only_rebase"] = {
        "prior_dataset_sha256": _sha(reviewed / "dataset.json"),
        "prior_results_sha256": _sha(reviewed / "results.jsonl"),
        "condition": "Exact tasks, attempt inventory and every reviewer-visible evidence binding were unchanged; independent agent verdicts were retained.",
    }
    result["total_attempts"] = len(current_rows)
    result["expected_attempts"] = len(current_rows)
    result["reviewed_attempts"] = len(current_rows)
    result["accepted_unreviewed_passes"] = 0
    result["disagreements"] = sum(row["agent_verdict"] != row["machine_verdict"] for row in attempts)
    result["unresolved"] = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if install:
        shutil.copy2(output, current / "result-review.json")
        require_result_review(current, current_dataset, current_rows)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-evidence", type=Path, required=True)
    parser.add_argument("--reviewed-evidence", type=Path, required=True)
    parser.add_argument("--review-file", type=Path,
                        help="Completed review file when it has not been installed in reviewed evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    review = rebase(args.current_evidence.resolve(), args.reviewed_evidence.resolve(),
                    args.output.resolve(), args.adjudications,
                    review_file=args.review_file.resolve() if args.review_file else None,
                    install=args.install)
    print(f"reviewed={review['reviewed_attempts']} disagreements={review['disagreements']} unresolved=0")
