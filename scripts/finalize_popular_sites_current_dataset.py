#!/usr/bin/env python3
"""Promote a reviewed 100-site evidence dataset to the current report matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from harness.web_fetch.metrics import summarize, task_passed
from harness.web_fetch.reference_lifecycle import (
    scoring_revision_history_sha256,
    valid_scoring_revision,
)
from harness.web_fetch.result_review import require_result_review

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "evaluation/popular-sites-dataset.json"
PROFILE = ROOT / "evaluation/current-report.json"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def passed_by_case(dataset: dict, records: list[dict]) -> dict[str, bool]:
    cases = {case["id"]: case for case in dataset["cases"]}
    return {
        row["case_id"]: task_passed(
            row["ok"], row["metrics"], cases[row["case_id"]].get("task_kind", "body")
        )
        for row in records
    }


def revision_details(
    prior_evidence: Path, dataset: dict, records: list[dict]
) -> tuple[list[dict], list[dict], int, int]:
    prior_dataset = json.loads((prior_evidence / "dataset.json").read_text())
    prior_records = [
        json.loads(line) for line in (prior_evidence / "results.jsonl").read_text().splitlines()
    ]
    require_result_review(prior_evidence, prior_dataset, prior_records)
    current_cases = {case["id"]: case for case in dataset["cases"]}
    prior_cases = {case["id"]: case for case in prior_dataset["cases"]}
    if set(current_cases) != set(prior_cases):
        raise ValueError("Rule revision must preserve the exact case inventory")
    current_rows = {row["attempt_id"]: row for row in records}
    prior_rows = {row["attempt_id"]: row for row in prior_records}
    if set(current_rows) != set(prior_rows):
        raise ValueError("Rule revision must preserve the exact attempt inventory")
    mutable_result_fields = {"metrics", "failure_category", "diagnosis", "scoring"}
    for attempt_id, row in current_rows.items():
        before = prior_rows[attempt_id]
        if {key: value for key, value in row.items() if key not in mutable_result_fields} != {
            key: value for key, value in before.items() if key not in mutable_result_fields
        }:
            raise ValueError(f"Provider-call identity changed: {attempt_id}")
    changed_cases = []
    for case_id in sorted(current_cases):
        before, after = prior_cases[case_id], current_cases[case_id]
        fields = sorted(
            key for key in set(before) | set(after) if before.get(key) != after.get(key)
        )
        if fields:
            changed_cases.append({"case_id": case_id, "fields": fields})
    before_pass = passed_by_case(prior_dataset, prior_records)
    after_pass = passed_by_case(dataset, records)
    decision_changes = [
        {
            "case_id": case_id,
            "from": "pass" if before_pass[case_id] else "fail",
            "to": "pass" if after_pass[case_id] else "fail",
        }
        for case_id in sorted(before_pass)
        if before_pass[case_id] != after_pass[case_id]
    ]
    return changed_cases, decision_changes, sum(before_pass.values()), sum(after_pass.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--prior-evidence",
        type=Path,
        help="Prior complete reviewed evidence when this is a score-only rule revision",
    )
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    prior_evidence = args.prior_evidence.resolve() if args.prior_evidence else None
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in (evidence / "results.jsonl").read_text().splitlines()]
    if dataset["id"] != "popular-sites" or len(dataset["cases"]) != 100 or len(records) != 100:
        raise ValueError("Expected the complete 100-site evidence matrix")
    require_result_review(evidence, dataset, records)
    revision = None
    if prior_evidence:
        revision = revision_details(prior_evidence, dataset, records)
        changed_cases, decision_changes, before_passes, after_passes = revision
        expected = {
            "global-homepage-010-bing-com",
            "global-homepage-080-cnn-com",
            "china-homepage-063-sina-com-cn",
            "china-homepage-080-yandex-com",
        }
        if {row["case_id"] for row in changed_cases} != expected:
            raise ValueError("Expected exactly the four accepted popular-site rule changes")
        if (before_passes, after_passes) != (42, 46):
            raise ValueError("Expected the accepted score correction from 42/100 to 46/100")
    comparison = dataset["comparison"]
    comparison.update(
        {
            "publication_state": "measured_independently_reviewed",
            "conversion": "Lexmount Global API native text plus structured name-link fields",
            "reference_binding_mode": "hash_bound_stable_text_and_structure",
            "unverified_reference_cases": [],
        }
    )
    write_json(evidence / "dataset.json", dataset)
    # Dataset bytes changed only in non-scoring report-state metadata. Rebind the
    # already complete review to the promoted dataset; results remain immutable.
    review = json.loads((evidence / "result-review.json").read_text())
    review["bindings"]["dataset_sha256"] = hashlib.sha256(
        (evidence / "dataset.json").read_bytes()
    ).hexdigest()
    review["review_protocol"]["metadata_promotion"] = {
        "cases_changed": False,
        "results_changed": False,
        "provider_calls": 0,
        "fields": [
            "publication_state",
            "conversion",
            "reference_binding_mode",
            "unverified_reference_cases",
        ],
    }
    write_json(evidence / "result-review.json", review)
    require_result_review(evidence, dataset, records)
    write_json(evidence / "summary.json", summarize(records))
    if revision and prior_evidence:
        changed_cases, decision_changes, before_passes, after_passes = revision
        scoring_path = evidence / "scoring-revisions.json"
        history = json.loads(scoring_path.read_text()) if scoring_path.is_file() else []
        history.append(
            {
                "kind": "audited_scoring_rule_revision",
                "scored_at": datetime.now(UTC).isoformat(),
                "input_dataset_sha256": sha256(prior_evidence / "dataset.json"),
                "input_results_sha256": sha256(prior_evidence / "results.jsonl"),
                "output_dataset_sha256": sha256(evidence / "dataset.json"),
                "output_results_sha256": sha256(evidence / "results.jsonl"),
                "changed_cases": changed_cases,
                "task_decision_changes": decision_changes,
                "score_change": {
                    "before_passes": before_passes,
                    "after_passes": after_passes,
                    "denominator": len(records),
                },
                "provider_calls": 0,
                "immutable_call_fields": (
                    "attempt identity, provider output bytes/hash, request status, "
                    "timestamps and elapsed time retained"
                ),
                "source_scoring_receipts_sha256": scoring_revision_history_sha256(history),
                "independent_result_review_sha256": sha256(evidence / "result-review.json"),
            }
        )
        write_json(scoring_path, history)
        if not valid_scoring_revision(evidence):
            raise ValueError("Final scoring revision receipt is not bound to current evidence")
    write_json(CANONICAL, dataset)
    profile = {
        "schema_version": 1,
        "dataset_id": "popular-sites",
        "dataset_file": "evaluation/popular-sites-dataset.json",
        "comparison_kind": "popular_sites",
        "case_count": 100,
        "providers": ["lexmount-global"],
        "timeout_seconds": 45,
        "runs_per_case": 1,
        "concurrency": 1,
        "reference_binding_mode": "hash_bound_stable_text_and_structure",
        "publication_state": "measured_independently_reviewed",
        "unverified_reference_cases": [],
        "require_independent_result_review": True,
        "conversion": "Lexmount Global API native text plus structured name-link fields",
        "artifact_hub_base_url": "https://artifact-hub.app.lexmount.com",
        "artifact_hub_site": "l9jZU6GPMLn6",
    }
    write_json(PROFILE, profile)
    print("Promoted reviewed 100-site evidence to evaluation/current-report.json")


if __name__ == "__main__":
    main()
