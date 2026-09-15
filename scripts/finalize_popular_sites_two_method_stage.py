#!/usr/bin/env python3
# ruff: noqa: E402, E501 -- repository imports follow explicit path setup; evidence prose is whole
"""Promote the reviewed 200-result two-method popular-sites stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import summarize, task_passed
from harness.web_fetch.reference_lifecycle import (
    scoring_revision_history_sha256,
    valid_scoring_revision,
)
from harness.web_fetch.result_review import require_result_review

PROVIDERS = {"moli-webfetch", "lexmount-global"}
DEFERRED_PROVIDERS = [
    "crawl4ai",
    "deepseek-harness-built-in",
    "cowagent-built-in",
    "langchain-webbase",
    "openclaw-built-in",
    "lexmount-cn",
    "cloudflare-markdown",
    "firecrawl",
    "tavily",
    "exa",
]
PROFILE = ROOT / "evaluation/current-report.json"


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
    evidence = args.evidence.resolve()
    prior = args.prior_evidence.resolve()
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in (evidence / "results.jsonl").read_text().splitlines()]
    prior_dataset = json.loads((prior / "dataset.json").read_text())
    prior_records = [
        json.loads(line) for line in (prior / "results.jsonl").read_text().splitlines()
    ]
    if dataset.get("id") != "popular-sites" or len(dataset.get("cases", [])) != 100:
        raise ValueError("Expected the corrected frozen 100-site dataset")
    if len(records) != 200 or {row["provider"] for row in records} != PROVIDERS:
        raise ValueError("Expected exactly 200 two-method results")
    if len({row["attempt_id"] for row in records}) != 200:
        raise ValueError("Attempt identities are not unique")
    review = require_result_review(evidence, dataset, records)
    if review["reviewed_attempts"] != 200 or review["unresolved"] != 0:
        raise ValueError("The full blind review is not resolved")
    before, after = verdicts(prior_dataset, prior_records), verdicts(dataset, records)
    expected_changes = {
        "lexmount-global__global-homepage-028-microsoft-com__r001": (True, False),
        "moli-webfetch__china-homepage-046-wedoany-com__r001": (False, True),
    }
    actual_changes = {
        attempt_id: (before[attempt_id], after[attempt_id])
        for attempt_id in before
        if before[attempt_id] != after[attempt_id]
    }
    if actual_changes != expected_changes:
        raise ValueError("Post-freeze correction changed an unexpected decision set")

    prior_dataset_hash = digest(evidence / "dataset.json")
    dataset["name"] = "全球热门网站 100 站 Web Fetch 两方法阶段评测"
    dataset["comparison"].update(
        {
            "publication_state": "measured_independently_reviewed",
            "stage_label": "2-method audited stage",
            "deferred_providers": DEFERRED_PROVIDERS,
            "fresh_formal_result_count": 103,
            "inherited_formal_result_count": 97,
            "provider_results_reused": 97,
            "score_only_recompute_count": 97,
            "required_new_provider_calls": 103,
        }
    )
    write_json(evidence / "dataset.json", dataset)
    review["bindings"]["dataset_sha256"] = digest(evidence / "dataset.json")
    review["review_protocol"]["metadata_promotion"] = {
        "cases_changed": False,
        "results_changed": False,
        "provider_calls": 0,
        "fields": [
            "name",
            "comparison.publication_state",
            "comparison.stage_label",
            "comparison.deferred_providers",
            "comparison result-count fields",
        ],
        "prior_dataset_sha256": prior_dataset_hash,
    }
    write_json(evidence / "result-review.json", review)
    require_result_review(evidence, dataset, records)
    write_json(evidence / "summary.json", summarize(records))

    revision_path = evidence / "scoring-revisions.json"
    revisions = json.loads(revision_path.read_text())
    revisions.append(
        {
            "kind": "audited_assembled_scoring_revision",
            "scored_at": datetime.now(UTC).isoformat(),
            "source_scoring_receipts_sha256": scoring_revision_history_sha256(revisions),
            "output_dataset_sha256": digest(evidence / "dataset.json"),
            "output_results_sha256": digest(evidence / "results.jsonl"),
            "independent_result_review_sha256": digest(evidence / "result-review.json"),
            "changed_cases": [
                {
                    "case_id": "china-homepage-046-wedoany-com",
                    "fields": ["acceptance_contract"],
                    "reason": "The requested and independently observed final host aliases are both accepted; stable text and detail path are unchanged.",
                }
            ],
            "task_decision_changes": [
                {
                    "attempt_id": attempt_id,
                    "from": "pass" if old else "fail",
                    "to": "pass" if new else "fail",
                }
                for attempt_id, (old, new) in sorted(actual_changes.items())
            ],
            "scope": "All 200 preserved outputs rescored under the approved post-freeze correction and fully blind-reviewed again.",
            "provider_calls": 0,
            "stable_text_changed": False,
            "immutable_call_fields": "Attempt identity, output bytes and hashes, request status, timestamps, and elapsed time retained.",
        }
    )
    write_json(revision_path, revisions)
    if not valid_scoring_revision(evidence):
        raise ValueError("Final scoring correction receipt is not bound to current evidence")

    profile = json.loads(PROFILE.read_text())
    profile.update(
        {
            "dataset_id": "popular-sites",
            "dataset_file": "evaluation/popular-sites-dataset.json",
            "comparison_kind": "popular_sites",
            "case_count": 100,
            "providers": ["moli-webfetch", "lexmount-global"],
            "timeout_seconds": 45,
            "runs_per_case": 1,
            "concurrency": 1,
            "reference_binding_mode": "hash_bound_stable_text_and_structure",
            "publication_state": "measured_independently_reviewed",
            "unverified_reference_cases": [],
            "require_independent_result_review": True,
            "formal_result_count": 200,
            "provider_results_reused": 97,
            "body_reference_case_count": 0,
            "body_metrics": "unavailable",
            "execution_contract": (
                "research/popular-sites-two-method-execution-contract-2026-09-15.json"
            ),
            "conversion": (
                "Each method's native known-URL fetch output is normalized once into text plus "
                "explicit structured links when available."
            ),
            "artifact_hub_site": None,
            "fresh_formal_result_count": 103,
            "inherited_formal_result_count": 97,
            "score_only_recompute_count": 97,
            "required_new_provider_calls": 103,
        }
    )
    write_json(PROFILE, profile)
    totals = {
        provider: sum(
            passed for attempt_id, passed in after.items() if attempt_id.startswith(provider + "__")
        )
        for provider in PROVIDERS
    }
    print(
        json.dumps(
            {
                "results": 200,
                "reviewed": 200,
                "disagreements": review["disagreements"],
                "unresolved": 0,
                "passes": totals,
                "new_provider_calls": 103,
                "dataset_sha256": digest(evidence / "dataset.json"),
                "results_sha256": digest(evidence / "results.jsonl"),
                "review_sha256": digest(evidence / "result-review.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
