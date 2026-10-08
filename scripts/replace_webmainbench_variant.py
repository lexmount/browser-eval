"""Replace one seven-method variant with a newly certified complete run.

The operation preserves every other provider byte-for-byte, verifies the
candidate through the public cohort/scoring boundaries, and emits a new raw
seven-method matrix ready for one shared scoring revision.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_certification import verify_shard
from harness.web_fetch.webmainbench_evidence import (
    alias_row,
    digest,
    read_jsonl,
    require_matrix,
)
from harness.web_fetch.webmainbench_scoring import verify_score

HISTORY_FILES = (
    "teds-correction.json", "teds-correction-review.json",
    "reference-correction.json", "scoring-correction.json",
    "pre-reference-correction-results.jsonl", "pre-scoring-correction-results.jsonl",
    "pre-scoring-correction-dataset.json", "case-correction.json",
    "case-correction-scoring.json", "superseded-case-results.jsonl",
    "superseded-case-calls.jsonl",
)


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def raw_row(row: dict) -> dict:
    """Remove earlier score-revision snapshots from a new raw matrix."""
    row = copy.deepcopy(row)
    row.pop("official_metrics", None)
    row.pop("pre_scoring_correction_metrics", None)
    return row


def replace(source: Path, candidate: Path, scorer: Path, profile_path: Path,
            provider: str, output: Path) -> None:
    if output.exists():
        raise ValueError("Replacement evidence is immutable")
    profile_path = profile_path.resolve()
    registry_path = ROOT / "evaluation/webmainbench-full/moli-variants.json"
    registry = json.loads(registry_path.read_text())
    registry_item = next((item for item in registry["variants"]
                          if item["provider"] == provider), None)
    if (registry_item is None
            or (ROOT / "evaluation/webmainbench-full" / registry_item["profile"]).resolve() != profile_path):
        raise ValueError("Replacement provider/profile is not registered")
    profile = json.loads(profile_path.read_text())
    cohort = json.loads((candidate / "cohort-certificate.json").read_text())
    scoring = json.loads((candidate / "scoring-certificate.json").read_text())
    dataset = json.loads((source / "dataset.json").read_text())
    case_ids = {case["id"] for case in dataset["cases"]}
    shard = candidate / "moli"
    if (cohort.get("profile_sha256") != digest(profile_path)
            or cohort.get("preflight") != cohort.get("postflight")
            or cohort.get("binary_source_commit") != profile.get("source_commit")
            or cohort.get("measured_binary_sha256") != profile.get("binary_sha256")
            or scoring.get("cohort_certificate_sha256") != digest(candidate / "cohort-certificate.json")
            or verify_shard(shard, "moli", case_ids, profile) != cohort["providers"][0]
            or verify_score(shard, "moli", case_ids, scorer, profile) != scoring["providers"][0]):
        raise ValueError("Replacement cohort or scoring binding differs")

    shutil.copytree(source, output)
    for name in HISTORY_FILES:
        (output / name).unlink(missing_ok=True)
    for name in ("correction-source", "reference-correction-source", "scoring-correction-source"):
        shutil.rmtree(output / name, ignore_errors=True)

    measured = [alias_row({**raw_row(row), "case_id": row["track_id"]}, "moli", provider)
                for row in read_jsonl(shard / "scored-results.jsonl")]
    calls = [alias_row(row, "moli", provider)
             for row in read_jsonl(shard / "physical-attempts.jsonl")]
    for row in read_jsonl(shard / "scored-results.jsonl"):
        source_artifact = contained_path(shard, row["artifact_path"])
        if digest(source_artifact) != row["output_sha256"]:
            raise ValueError("Replacement logical artifact changed")
        relative = row["artifact_path"].replace("artifacts/moli/", f"artifacts/{provider}/", 1)
        target = contained_path(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_artifact, target)
    for row in read_jsonl(shard / "physical-attempts.jsonl"):
        source_artifact = contained_path(shard, row["physical_artifact_path"])
        if digest(source_artifact) != row["output_sha256"]:
            raise ValueError("Replacement physical artifact changed")
        relative = row["physical_artifact_path"].replace("artifacts/moli/", f"artifacts/{provider}/", 1)
        target = contained_path(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_artifact, target)

    old_rows = [raw_row(row) for row in read_jsonl(source / "results.jsonl")
                if row["provider"] != provider]
    rows = old_rows + measured
    providers = dataset["coverage_plan"]["static_html"]
    require_matrix(rows, providers, case_ids)
    write_rows(output / "results.jsonl", rows)
    write_rows(output / "original-results.jsonl", rows)
    physical = [row for row in read_jsonl(source / "physical-attempts.jsonl")
                if row["provider"] != provider] + calls
    if len({row["attempt_id"] for row in physical}) != len(physical):
        raise ValueError("Replacement physical attempt identity is not unique")
    write_rows(output / "physical-attempts.jsonl", physical)

    summaries = json.loads((source / "score-summaries.json").read_text())
    summaries[provider] = {**json.loads((shard / "score-summary.json").read_text()),
                           "provider": provider}
    for summary in summaries.values():
        summary.pop("original_official_aggregate", None)
        summary.pop("pre_scoring_correction_aggregate", None)
    (output / "score-summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n")

    execution = json.loads((source / "execution.json").read_text())
    execution["batches"] = [batch for batch in execution["batches"]
                            if batch.get("provider") != provider]
    replacement_execution = json.loads((shard / "execution.json").read_text())
    replacement_execution["provider"] = provider
    execution["batches"].append(replacement_execution)
    (output / "execution.json").write_text(
        json.dumps(execution, ensure_ascii=False, indent=2) + "\n")

    profile_name = registry_item["profile"]
    cohort_name = f"{provider}-cohort-certificate.json"
    scoring_name = f"{provider}-scoring-certificate.json"
    shutil.copy2(profile_path, output / profile_name)
    shutil.copy2(candidate / "cohort-certificate.json", output / cohort_name)
    shutil.copy2(candidate / "scoring-certificate.json", output / scoring_name)
    binding = json.loads((source / "seven-method-binding.json").read_text())
    binding["results_sha256"] = digest(output / "results.jsonl")
    binding["physical_attempts_sha256"] = digest(output / "physical-attempts.jsonl")
    binding["physical_calls"] = len(physical)
    for item in binding["variants"]:
        if item["provider"] == provider:
            item.update({
                "profile_sha256": digest(profile_path),
                "cohort_certificate": cohort_name,
                "cohort_certificate_sha256": digest(candidate / "cohort-certificate.json"),
                "scoring_certificate": scoring_name,
                "scoring_certificate_sha256": digest(candidate / "scoring-certificate.json"),
                "source_results_sha256": digest(shard / "scored-results.jsonl"),
            })
            item.pop("source_six_method_binding_sha256", None)
    (output / "seven-method-binding.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--scorer", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    replace(args.source, args.candidate, args.scorer, args.profile, args.provider, args.output)


if __name__ == "__main__":
    main()
