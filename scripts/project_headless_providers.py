"""Project reviewed evidence to the providers declared by the current report.

The source bundle is never changed. Removed provider calls remain in its archive;
the output gets new hashes and an explicit review/revision projection receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.reference_lifecycle import scoring_revision_history_sha256
from harness.web_fetch.result_review import require_result_review


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_manifest(document: dict, kept_attempts: set[str], providers: set[str]) -> dict:
    result = json.loads(json.dumps(document))
    attempts = [value for value in result.get("attempt_ids", []) if value in kept_attempts]
    result["attempt_ids"] = attempts
    result["attempt_count"] = len(attempts)
    if isinstance(result.get("providers"), list):
        result["providers"] = [
            value for value in result["providers"]
            if (value.get("name") if isinstance(value, dict) else value) in providers
        ]
    result.setdefault("projection", {})["current_report_provider_scope"] = sorted(providers)
    return result


def project_bundle(source: Path, output: Path, provider_order: list[str], *,
                   conversion: str | None = None) -> None:
    if output.exists():
        raise ValueError(f"Projection destination exists: {output}")
    dataset = json.loads((source / "dataset.json").read_text())
    records = [json.loads(line) for line in (source / "results.jsonl").read_text().splitlines()]
    require_result_review(source, dataset, records)
    available = {record["provider"] for record in records}
    providers = set(provider_order)
    removed_providers = sorted(available - providers)
    if not providers or not providers <= available:
        raise ValueError("Requested provider projection is absent from the source matrix")
    projected = [record for record in records if record["provider"] in providers]
    expected = {(case["id"], provider) for case in dataset["cases"] for provider in provider_order}
    if len(projected) != len(expected) or {
        (record["case_id"], record["provider"]) for record in projected
    } != expected:
        raise ValueError("Source does not contain one complete retained provider/case matrix")

    shutil.copytree(source, output)
    for track, names in dataset.get("coverage_plan", {}).items():
        retained = [name for name in provider_order if name in names]
        if retained != provider_order:
            raise ValueError(f"Provider projection is incomplete for track {track}")
        dataset["coverage_plan"][track] = retained
    if conversion is not None:
        dataset.setdefault("comparison", {})["conversion"] = conversion
    (output / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "results.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=True) + "\n" for record in projected)
    )
    kept_attempts = {record["attempt_id"] for record in projected}

    execution_path = output / "execution.json"
    if execution_path.is_file():
        execution = json.loads(execution_path.read_text())
        execution["batches"] = [
            batch for original in execution["batches"]
            if (batch := _project_manifest(original, kept_attempts, providers))["attempt_ids"]
        ]
        execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n")
    manifest_path = output / "manifest.json"
    if manifest_path.is_file():
        manifest_path.write_text(json.dumps(
            _project_manifest(json.loads(manifest_path.read_text()), kept_attempts, providers),
            ensure_ascii=False, indent=2,
        ) + "\n")

    source_review_path = source / "result-review.json"
    review = json.loads(source_review_path.read_text())
    review["attempts"] = [
        row for row in review.get("attempts", []) if row.get("attempt_id") in kept_attempts
    ]
    protocol = review.setdefault("review_protocol", {})
    protocol["provider_filter"] = provider_order
    protocol.pop("shards", None)
    review["projection"] = {
        "kind": "reviewed_provider_subset",
        "source_review_sha256": digest(source_review_path),
        "removed_providers": removed_providers,
        "provider_calls": 0,
        "condition": "retained task definitions, attempt records and review-visible artifacts are byte-identical",
    }
    review["bindings"] = {
        "dataset_sha256": digest(output / "dataset.json"),
        "results_sha256": digest(output / "results.jsonl"),
    }
    review["expected_attempts"] = len(review["attempts"])
    review["reviewed_attempts"] = len(review["attempts"])
    review["total_attempts"] = len(projected)
    review["accepted_unreviewed_passes"] = 0
    review_path = output / "result-review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    require_result_review(output, dataset, projected)

    results_hash = digest(output / "results.jsonl")
    dataset_hash = digest(output / "dataset.json")
    reference_path = output / "reference-revisions.json"
    if reference_path.is_file():
        receipts = json.loads(reference_path.read_text())
        for receipt in receipts:
            receipt["output_dataset_sha256"] = dataset_hash
            receipt["output_results_sha256"] = results_hash
            receipt["projection"] = "reviewed current-report provider subset; no provider calls"
        reference_path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + "\n")

    latency_path = output / "latency-corrections.json"
    if latency_path.is_file():
        receipt = json.loads(latency_path.read_text())
        receipt["changes"] = [
            change for change in receipt.get("changes", []) if change.get("provider") in providers
        ]
        if isinstance(receipt.get("diagnostic_summary"), dict):
            receipt["diagnostic_summary"] = {
                name: value for name, value in receipt["diagnostic_summary"].items()
                if name in providers
            }
        receipt["superseded_logical_results"] = len(receipt["changes"])
        receipt["output_results_sha256"] = results_hash
        receipt["selection"] = (
            "reviewed final-round latency correction projected to retained providers; "
            f"{len(receipt['changes'])} logical results retained; no new provider calls"
        )
        receipt["projection"] = {
            "kind": "reviewed_provider_subset",
            "removed_providers": removed_providers,
            "provider_calls": 0,
        }
        latency_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")

    scoring_path = output / "scoring-revisions.json"
    history = json.loads(scoring_path.read_text()) if scoring_path.is_file() else []
    history.append({
        "kind": "audited_assembled_scoring_revision",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": dataset_hash,
        "input_results_sha256": results_hash,
        "output_dataset_sha256": dataset_hash,
        "output_results_sha256": results_hash,
        "changed_cases": [],
        "task_decision_changes": [],
        "provider_calls": 0,
        "immutable_call_fields": "retained task inputs, attempt records, output bytes, scores and verdicts unchanged",
        "source_scoring_receipts_sha256": scoring_revision_history_sha256(history),
        "independent_result_review_sha256": digest(review_path),
        "projection": "reviewed current-report provider subset; removed providers: "
        + (", ".join(removed_providers) or "none"),
    })
    scoring_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=ROOT / "evaluation/current-report.json")
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    project_bundle(args.source, args.output, profile["providers"],
                   conversion=profile["conversion"])
    source_speed = args.source / "speed-cohort"
    if source_speed.is_dir():
        projected_speed = args.output / "speed-cohort"
        shutil.rmtree(projected_speed)
        project_bundle(source_speed, projected_speed, profile["providers"])
        (projected_speed / "speed-comparison.json").unlink(missing_ok=True)
    print(f"Projected {len(profile['providers'])} providers to {args.output}")


if __name__ == "__main__":
    main()
