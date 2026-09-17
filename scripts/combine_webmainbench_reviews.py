"""Bind the prior and added method audits to the complete result matrix."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--prior-review", type=Path, required=True)
    parser.add_argument("--new-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reviews = [json.loads(path.read_text(encoding="utf-8"))
               for path in (args.prior_review, args.new_review)]
    dataset = json.loads((args.evidence / "dataset.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (args.evidence / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    providers = dataset["coverage_plan"]["static_html"]
    groups = [set(review.get("providers", [])) for review in reviews]
    if (any(review.get("status") != "passed" or review.get("failures") for review in reviews)
            or groups[0] & groups[1] or groups[0] | groups[1] != set(providers)
            or any(review.get("sample_case_count") != len(dataset["cases"]) or
                   review.get("sample_manifest_sha256") != dataset["sample_manifest_sha256"]
                   for review in reviews)
            or len(rows) != len(providers) * len(dataset["cases"])
            or {(row["provider"], row["case_id"]) for row in rows} != {
                (provider, case["id"]) for provider in providers for case in dataset["cases"]
            }):
        raise ValueError("Review scopes do not certify the complete measured matrix")
    result = {
        "schema_version": 2,
        "status": "passed",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewer": "independent deterministic evidence, score and retry audit across both measurement cohorts",
        "providers": providers,
        "sample_case_count": len(dataset["cases"]),
        "sample_manifest_sha256": dataset["sample_manifest_sha256"],
        "dataset_sha256": dataset["source_dataset_sha256"],
        "scope": {"logical_results": len(rows), "audited_results": len(rows),
                  "human_content_reviewed": 0},
        "components": [
            {"providers": review["providers"], "review_sha256": digest(path),
             "review_script_sha256": review["review_script_sha256"]}
            for path, review in zip((args.prior_review, args.new_review), reviews)
        ],
        "checks": [check for review in reviews for check in review["checks"]],
        "provider_notes": {name: note for review in reviews
                           for name, note in review["provider_notes"].items()},
        "bindings": {"dataset_json_sha256": digest(args.evidence / "dataset.json"),
                     "results_jsonl_sha256": digest(args.evidence / "results.jsonl"),
                     "execution_json_sha256": digest(args.evidence / "execution.json")},
        "failures": [],
    }
    if args.output.exists() and args.output.resolve() != (args.evidence / "result-review.json").resolve():
        raise ValueError("Refusing to replace an unrelated review")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], **result["scope"]}))


if __name__ == "__main__":
    main()
