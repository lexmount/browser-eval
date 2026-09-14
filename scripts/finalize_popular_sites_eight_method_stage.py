#!/usr/bin/env python3
"""Promote the reviewed 8-method/100-site stage and record its offline correction."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import summarize, task_passed
from harness.web_fetch.reference_lifecycle import valid_scoring_revision
from harness.web_fetch.result_review import require_result_review


PROVIDERS = {
    "crawl4ai", "moli-webfetch", "deepseek-harness-built-in",
    "cowagent-built-in", "langchain-webbase", "openclaw-built-in",
    "lexmount-cn", "lexmount-global",
}
EXPECTED_CHANGES = {
    "cowagent-built-in__global-homepage-041-discord-com__r001",
    "moli-webfetch__global-homepage-060-espn-com__r001",
    "moli-webfetch__china-homepage-067-alibaba-com__r001",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def verdicts(dataset: dict, records: list[dict]) -> dict[str, bool]:
    cases = {case["id"]: case for case in dataset["cases"]}
    return {
        row["attempt_id"]: task_passed(
            row["ok"], row["metrics"], cases[row["case_id"]].get("task_kind", "body")
        )
        for row in records
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--prior-evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence, prior = args.evidence.resolve(), args.prior_evidence.resolve()
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in (evidence / "results.jsonl").read_text().splitlines()]
    prior_dataset = json.loads((prior / "dataset.json").read_text())
    prior_records = [json.loads(line) for line in (prior / "results.jsonl").read_text().splitlines()]
    if dataset.get("id") != "popular-sites" or len(dataset.get("cases", [])) != 100:
        raise ValueError("Expected the frozen 100-site dataset")
    if len(records) != 800 or {row["provider"] for row in records} != PROVIDERS:
        raise ValueError("Expected exactly 800 authoritative results from the fixed eight methods")
    if len({row["attempt_id"] for row in records}) != 800:
        raise ValueError("Attempt identities are not unique")
    require_result_review(evidence, dataset, records)

    before, after = verdicts(prior_dataset, prior_records), verdicts(dataset, records)
    if before.keys() != after.keys():
        raise ValueError("Offline scoring correction changed the attempt inventory")
    mutable = {"metrics", "diagnosis", "failure_category", "scoring"}
    prior_by_id = {row["attempt_id"]: row for row in prior_records}
    for row in records:
        old = prior_by_id[row["attempt_id"]]
        if ({key: value for key, value in row.items() if key not in mutable}
                != {key: value for key, value in old.items() if key not in mutable}):
            raise ValueError("Provider-call evidence changed: " + row["attempt_id"])
        if row["metrics"]["content_sha256"] != old["metrics"]["content_sha256"]:
            raise ValueError("Provider output hash changed: " + row["attempt_id"])
    changed = {attempt_id for attempt_id in before if before[attempt_id] != after[attempt_id]}
    if changed != EXPECTED_CHANGES or (sum(before.values()), sum(after.values())) != (211, 214):
        raise ValueError("Expected the audited 211/800 to 214/800 scoring correction")

    if dataset["comparison"].get("publication_state") != "measured_independently_reviewed":
        prior_dataset_hash = digest(evidence / "dataset.json")
        dataset["comparison"]["publication_state"] = "measured_independently_reviewed"
        write_json(evidence / "dataset.json", dataset)
        review = json.loads((evidence / "result-review.json").read_text())
        review["bindings"]["dataset_sha256"] = digest(evidence / "dataset.json")
        review["review_protocol"]["metadata_promotion"] = {
            "cases_changed": False,
            "results_changed": False,
            "provider_calls": 0,
            "fields": ["comparison.publication_state"],
            "prior_dataset_sha256": prior_dataset_hash,
        }
        write_json(evidence / "result-review.json", review)
    require_result_review(evidence, dataset, records)
    write_json(evidence / "summary.json", summarize(records))

    decision_changes = [
        {"attempt_id": attempt_id, "from": "pass" if before[attempt_id] else "fail",
         "to": "pass" if after[attempt_id] else "fail"}
        for attempt_id in sorted(changed)
    ]
    receipt = {
        "kind": "audited_scoring_rule_revision",
        "scored_at": datetime.now(UTC).isoformat(),
        "input_dataset_sha256": digest(prior / "dataset.json"),
        "input_results_sha256": digest(prior / "results.jsonl"),
        "output_dataset_sha256": digest(evidence / "dataset.json"),
        "output_results_sha256": digest(evidence / "results.jsonl"),
        "changed_cases": [],
        "task_decision_changes": decision_changes,
        "score_change": {"before_passes": 211, "after_passes": 214, "denominator": 800},
        "scope": "all 800 persisted outputs rescored with the shared scorer",
        "reason": (
            "Recognize HTML character entities as visible text and parse link-dense flattened "
            "Markdown collections without treating ordinary prose links as records."
        ),
        "provider_calls": 0,
        "immutable_call_fields": (
            "attempt identity, provider output bytes/hash, request status, timestamps and elapsed time retained"
        ),
        "independent_result_review_sha256": digest(evidence / "result-review.json"),
    }
    write_json(evidence / "scoring-revisions.json", [receipt])
    if not valid_scoring_revision(evidence):
        raise ValueError("Scoring correction receipt is not bound to current evidence")

    write_json(ROOT / "evaluation/popular-sites-dataset.json", dataset)
    profile_path = ROOT / "evaluation/current-report.json"
    profile = json.loads(profile_path.read_text())
    profile["publication_state"] = "measured_independently_reviewed"
    profile["formal_result_count"] = 800
    write_json(profile_path, profile)
    print(json.dumps({
        "results": 800, "reviewed": 800, "disagreements": 0, "unresolved": 0,
        "passes": 214, "provider_calls_for_correction": 0,
        "dataset_sha256": digest(evidence / "dataset.json"),
        "results_sha256": digest(evidence / "results.jsonl"),
        "review_sha256": digest(evidence / "result-review.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
