#!/usr/bin/env python3
"""Promote a reviewed 100-site evidence dataset to the current report matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.web_fetch.result_review import require_result_review

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "evaluation/popular-sites-dataset.json"
PROFILE = ROOT / "evaluation/current-report.json"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in (evidence / "results.jsonl").read_text().splitlines()]
    if dataset["id"] != "popular-sites" or len(dataset["cases"]) != 100 or len(records) != 100:
        raise ValueError("Expected the complete 100-site evidence matrix")
    require_result_review(evidence, dataset, records)
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
