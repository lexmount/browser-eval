"""Assemble five immutable WebMainBench provider shards into one evidence bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


PROVIDERS = ("kitesurf", "lightpanda", "obscura", "moli", "chromium")
DATASET_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output exists; assembled evidence is immutable")
    if digest(args.dataset) != DATASET_SHA256:
        raise ValueError("WebMainBench dataset hash mismatch")
    review = json.loads(args.review.read_text(encoding="utf-8"))
    if review.get("status") != "passed":
        raise ValueError("Independent result review has not passed")
    source_records = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(source_records) != 545:
        raise ValueError("WebMainBench source must contain 545 rows")
    sample = json.loads(args.sample_manifest.read_text(encoding="utf-8"))
    selected_ids = {row.get("track_id") for row in sample.get("cases", [])}
    sample_count = sample.get("sampling", {}).get("size")
    sample_sha256 = digest(args.sample_manifest)
    if (sample.get("schema_version") != 1
            or sample.get("parent", {}).get("sha256") != DATASET_SHA256
            or sample_count != len(sample.get("cases", []))
            or None in selected_ids or len(selected_ids) != sample_count):
        raise ValueError("Invalid WebMainBench sample manifest")
    if review.get("sample_manifest_sha256") != sample_sha256:
        raise ValueError("Review is not bound to the selected sample")
    source_records = [record for record in source_records if record["track_id"] in selected_ids]
    if len(source_records) != sample_count:
        raise ValueError("Sample contains unknown WebMainBench track IDs")

    results: list[dict] = []
    physical: list[dict] = []
    summaries = {}
    executions = []
    args.output.mkdir(parents=True)
    (args.output / "artifacts").mkdir()
    (args.output / "references").mkdir()
    (args.output / "fixture-certificates").mkdir()
    for provider in PROVIDERS:
        shard = args.source_root / provider
        rows = read_jsonl(shard / "scored-results.jsonl")
        attempts = read_jsonl(shard / "physical-attempts.jsonl")
        if len(rows) != sample_count or {row["provider"] for row in rows} != {provider}:
            raise ValueError(f"{provider}: incomplete scored result shard")
        if {row["track_id"] for row in rows} != {record["track_id"] for record in source_records}:
            raise ValueError(f"{provider}: case matrix mismatch")
        for row in rows:
            artifact = shard / row["artifact_path"]
            if digest(artifact) != row["output_sha256"]:
                raise ValueError(f"{provider}: artifact hash mismatch for {row['track_id']}")
            target = args.output / row["artifact_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifact, target)
            results.append({**row, "case_id": row["track_id"]})
        physical.extend(attempts)
        summaries[provider] = json.loads((shard / "score-summary.json").read_text(encoding="utf-8"))
        execution = json.loads((shard / "execution.json").read_text(encoding="utf-8"))
        if execution.get("fixture_ca_artifact"):
            source_ca = shard / execution["fixture_ca_artifact"]
            target_ca = args.output / "fixture-certificates" / f"{provider}-ca.pem"
            shutil.copy2(source_ca, target_ca)
            execution["assembled_fixture_ca_path"] = str(target_ca.relative_to(args.output))
        executions.append(execution)

    if len(results) != sample_count * len(PROVIDERS):
        raise ValueError("Assembled logical result count mismatch")
    cases = [
        {
            "id": record["track_id"],
            "name": record["track_id"],
            "url": record["url"],
            "category": "webmainbench",
            "track": "static_html",
            "meta": record["meta"],
            "input_html_sha256": hashlib.sha256(record["html"].encode("utf-8")).hexdigest(),
            "reference_sha256": hashlib.sha256(record["groundtruth_content"].encode("utf-8")).hexdigest(),
            "reference_chars": len(record["groundtruth_content"]),
        }
        for record in source_records
    ]
    for record in source_records:
        (args.output / "references" / f"{record['track_id']}.md").write_text(
            record["groundtruth_content"], encoding="utf-8"
        )
    dataset = {
        "schema_version": 1,
        "tool": "web_fetch",
        "id": "webmainbench-545-preselected-200-five-browsers",
        "name": "WebMainBench-545 preselected 200 · five browser Web Fetch methods",
        "source_dataset_sha256": DATASET_SHA256,
        "sample_manifest_sha256": sample_sha256,
        "sample": {
            "dataset_id": sample.get("dataset_id"),
            "sampling": sample.get("sampling"),
            "parent_distribution": sample.get("parent_distribution"),
            "sample_distribution": sample.get("sample_distribution"),
            "quality_audit": sample.get("quality_audit"),
        },
        "cases": cases,
        "coverage_plan": {"static_html": list(PROVIDERS)},
        "comparison": {
            "kind": "webmainbench_frozen_html_to_markdown",
            "timeout_seconds": 45,
            "runs_per_case": 1,
            "concurrency": 1,
            "max_failure_retries": 1,
            "source_dataset_sha256": DATASET_SHA256,
            "sample_manifest_sha256": sample_sha256,
            "official_scorer_commit": "9d991bdc00c57b57521499494d96be85c31317ba",
            "official_metrics": ["overall", "text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS"],
            "metric_config": {"use_llm": False, "reason": "deterministic official baseline mode; no model-dependent formula refinement"},
            "input_contract": {
                "body": "official html field encoded as UTF-8 without mutation",
                "execution": "inline scripts may run; non-loopback subresources and data access are blocked; local engines receive the same body from an OS-isolated loopback HTTPS fixture with a per-run CA, Kitesurf receives the same body through Cloudflare Browser Rendering html input with external resource types rejected",
                "original_url": "retained as evidence metadata only",
            },
            "reference_binding_mode": "preselected_webmainbench_545_sample_200_official_frozen_html_and_groundtruth_content",
            "publication_state": "verified",
            "unverified_reference_cases": [],
            "require_independent_result_review": True,
            "conversion": "Native Markdown for Kitesurf, Lightpanda, Obscura and Moli; Chromium whole-DOM HTML via node-html-markdown 2.0.0",
        },
    }
    (args.output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "score-summaries.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "execution.json").write_text(json.dumps({"batches": executions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(args.review, args.output / "result-review.json")
    shutil.copy2(args.sample_manifest, args.output / "sample-manifest.json")
    with (args.output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for row in results:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (args.output / "physical-attempts.jsonl").open("w", encoding="utf-8") as stream:
        for row in physical:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Assembled {len(results)} logical results and {len(physical)} physical attempts")


if __name__ == "__main__":
    main()
