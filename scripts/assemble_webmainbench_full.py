"""Package only certified, scored WebMainBench-545 evidence for the full report."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.safety import contained_path
from scripts.run_certified_webmainbench_full import digest, read_jsonl, verify_shard
from scripts.run_webmainbench_benchmark import load_source


def public_value(value):
    """Remove machine-specific absolute paths from the public evidence copy."""
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_value(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"/Users/[^/\s:]+", "/redacted-home", value)
        value = re.sub(r"/home/[^/\s:]+", "/redacted-home", value)
        value = re.sub(r"/private/tmp/[^\s:]+", "/redacted-temp", value)
        value = re.sub(r"/(?:var/)?tmp/[^\s:]+", "/redacted-temp", value)
    return value


def copy_bound(root: Path, output: Path, relative: str, expected: str) -> None:
    source = contained_path(root, relative)
    if (root / relative).is_symlink() or digest(source) != expected:
        raise ValueError("Evidence artifact changed: " + relative)
    target = contained_path(output, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def assemble(root: Path, output: Path, source: Path, profile_path: Path) -> None:
    if output.exists():
        raise ValueError("Output already exists; report evidence is immutable")
    if root.is_symlink() or source.is_symlink() or profile_path.is_symlink():
        raise ValueError("Symlinked aggregate input is forbidden")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    cert_path = root / "cohort-certificate.json"
    certificate = json.loads(cert_path.read_text(encoding="utf-8"))
    scoring_certificate_path = root / "scoring-certificate.json"
    scoring_certificate = json.loads(scoring_certificate_path.read_text(encoding="utf-8"))
    if (certificate.get("kind") != "certified_webmainbench_545_full_v1"
            or certificate.get("profile_sha256") != digest(profile_path)
            or certificate.get("preflight") != certificate.get("postflight")
            or certificate["preflight"]["source_sha256"] != profile["source_dataset_sha256"]
            or [row["provider"] for row in certificate["providers"]] != profile["providers"]):
        raise ValueError("Complete-cohort certificate or profile mismatch")
    if (scoring_certificate.get("kind") != "certified_webmainbench_scoring_v1"
            or scoring_certificate.get("cohort_certificate_sha256") != digest(cert_path)
            or scoring_certificate.get("source_binding") != certificate["postflight"]
            or [row["provider"] for row in scoring_certificate["providers"]] != profile["providers"]):
        raise ValueError("Complete scoring certificate differs from the certified cohort")
    source_records = load_source(source)
    ids = {row["track_id"] for row in source_records}
    if len(ids) != 545 or len(source_records) != profile["case_count"]:
        raise ValueError("Source case inventory mismatch")
    results: list[dict] = []
    physical: list[dict] = []
    summaries: dict[str, dict] = {}
    executions: list[dict] = []
    output.mkdir(parents=True)
    (output / "references").mkdir()
    (output / "sources").mkdir()
    (output / "fixture-certificates").mkdir()
    for record in source_records:
        case_id = record["track_id"]
        (output / "references" / (case_id + ".md")).write_text(
            record["groundtruth_content"], encoding="utf-8")
        (output / "sources" / (case_id + ".html.txt")).write_text(
            record["html"], encoding="utf-8")
    for expected, scored_receipt in zip(certificate["providers"], scoring_certificate["providers"]):
        provider = expected["provider"]
        shard = root / provider
        if shard.is_symlink() or any(path.is_symlink() for path in shard.rglob("*")):
            raise ValueError(provider + ": symlinked evidence is forbidden")
        if verify_shard(shard, provider, ids, profile) != expected:
            raise ValueError(provider + ": certificate differs from measured shard")
        rows = read_jsonl(shard / "scored-results.jsonl")
        if (scored_receipt["scored_results_sha256"] != digest(shard / "scored-results.jsonl")
                or scored_receipt["score_summary_sha256"] != digest(shard / "score-summary.json")
                or scored_receipt["logical_results"] != profile["case_count"]):
            raise ValueError(provider + ": scoring certificate differs from scored shard")
        if (len(rows) != 545 or {row["track_id"] for row in rows} != ids
                or len({row["track_id"] for row in rows}) != 545
                or any(row["provider"] != provider for row in rows)):
            raise ValueError(provider + ": incomplete scored matrix")
        summary = json.loads((shard / "score-summary.json").read_text(encoding="utf-8"))
        if (summary.get("case_count") != 545 or summary.get("provider") != provider
                or summary.get("scorer", {}).get("metric_source_sha256") !=
                scoring_certificate["scorer_source_sha256"]):
            raise ValueError(provider + ": score summary scope mismatch")
        logical = {row["track_id"]: row for row in read_jsonl(shard / "results.jsonl")}
        for row in rows:
            base = logical[row["track_id"]]
            if {k: v for k, v in row.items() if k != "metrics"} != base:
                raise ValueError(provider + ": scoring changed measured result")
            copy_bound(shard, output, row["artifact_path"], row["output_sha256"])
            results.append(public_value({**row, "case_id": row["track_id"]}))
        attempts = read_jsonl(shard / "physical-attempts.jsonl")
        for attempt in attempts:
            copy_bound(shard, output, attempt["physical_artifact_path"], attempt["output_sha256"])
        physical.extend(public_value(attempts))
        execution = json.loads((shard / "execution.json").read_text(encoding="utf-8"))
        ca = execution.get("fixture_ca_artifact")
        if ca:
            ca_source = contained_path(shard, ca)
            if digest(ca_source) != execution["network_isolation_probe"]["fixture_ca_sha256"]:
                raise ValueError(provider + ": fixture CA changed")
            ca_target = output / "fixture-certificates" / (provider + "-ca.pem")
            shutil.copy2(ca_source, ca_target)
            execution["assembled_fixture_ca_path"] = str(ca_target.relative_to(output))
        executions.append(execution)
        summaries[provider] = summary
    expected_pairs = {(provider, case_id) for provider in profile["providers"] for case_id in ids}
    if (len(results) != len(expected_pairs)
            or {(row["provider"], row["case_id"]) for row in results} != expected_pairs
            or len({row["attempt_id"] for row in results}) != len(results)
            or len({row["attempt_id"] for row in physical}) != len(physical)):
        raise ValueError("Full result or physical attempt matrix mismatch")
    cases = [{"id": row["track_id"], "name": row["track_id"], "url": row["url"],
              "category": "webmainbench", "track": "static_html", "meta": row["meta"],
              "input_html_sha256": hashlib.sha256(row["html"].encode()).hexdigest(),
              "reference_sha256": hashlib.sha256(row["groundtruth_content"].encode()).hexdigest(),
              "reference_chars": len(row["groundtruth_content"])} for row in source_records]
    dataset = {"schema_version": 1, "tool": "web_fetch", "id": profile["dataset_id"],
               "name": "WebMainBench-545 full · five Web Fetch methods",
               "source_dataset_sha256": profile["source_dataset_sha256"],
               "cases": cases, "coverage_plan": {"static_html": profile["providers"]},
               "comparison": profile}
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "score-summaries.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "execution.json").write_text(json.dumps({"batches": executions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (output / "physical-attempts.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in physical), encoding="utf-8")
    shutil.copy2(cert_path, output / "cohort-certificate.json")
    shutil.copy2(scoring_certificate_path, output / "scoring-certificate.json")
    shutil.copy2(profile_path, output / "current-report.json")
    print(f"Assembled {len(results)} results and {len(physical)} physical calls", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=ROOT / "evaluation/webmainbench-full/current-report.json")
    args = parser.parse_args()
    assemble(args.source_root, args.output, args.dataset, args.profile)


if __name__ == "__main__":
    main()
