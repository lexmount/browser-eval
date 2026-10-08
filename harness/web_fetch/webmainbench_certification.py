"""Run the predeclared complete WebMainBench cohort with source and result checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from harness.web_fetch.config import load_runtime_env
from harness.web_fetch.webmainbench_evidence import digest, read_jsonl
from harness.web_fetch.webmainbench_runner import load_source

ROOT = Path(__file__).resolve().parents[2]






def source_binding(source: Path, scorer: Path, profile: dict) -> dict:
    if digest(source) != profile["source_dataset_sha256"]:
        raise ValueError("WebMainBench source bytes changed")
    records = load_source(source, expected_sha256=profile["source_dataset_sha256"])
    case_ids = [record["track_id"] for record in records]
    case_digest = hashlib.sha256(("\n".join(case_ids) + "\n").encode()).hexdigest()
    if len(records) != profile["case_count"] or case_digest != profile["case_ids_sha256"]:
        raise ValueError("WebMainBench case inventory changed")
    commit = subprocess.check_output(["git", "-C", str(scorer), "rev-parse", "HEAD"], text=True).strip()
    if commit != profile["official_scorer_commit"]:
        raise ValueError("Official scorer commit changed")
    return {"source_sha256": digest(source), "case_ids_sha256": case_digest,
            "scorer_commit": commit, "case_count": len(records)}


def verify_declared_binary(profile: dict) -> None:
    if not profile.get('binary_sha256'):
        return
    load_runtime_env()
    binary = shutil.which(os.getenv('MOLI_BINARY') or 'moli')
    if binary is None or digest(Path(binary)) != profile['binary_sha256']:
        raise ValueError('Declared Moli binary differs before provider calls')


def verify_source_checkout(profile: dict, checkout: Path | None) -> None:
    source_commit = profile.get('source_commit')
    if not source_commit:
        return
    if checkout is None:
        raise ValueError('Candidate binary source checkout is required')
    actual_commit = subprocess.check_output(
        ['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(
        ['git', '-C', str(checkout), 'status', '--porcelain'], text=True).strip()
    if actual_commit != source_commit or dirty:
        raise ValueError('Candidate binary source differs from declared clean commit')


def verify_shard(shard: Path, provider: str, case_ids: set[str], profile: dict) -> dict:
    rows = read_jsonl(shard / "results.jsonl")
    physical = read_jsonl(shard / "physical-attempts.jsonl")
    execution = json.loads((shard / "execution.json").read_text(encoding="utf-8"))
    dataset = json.loads((shard / "dataset.json").read_text(encoding="utf-8"))
    if (len(rows) != profile["case_count"] or {row["track_id"] for row in rows} != case_ids
            or len({row["track_id"] for row in rows}) != len(rows)
            or dataset["case_count"] != profile["case_count"]
            or set(dataset["case_ids"]) != case_ids
            or execution["case_count"] != profile["case_count"]
            or execution["provider"] != provider
            or execution["source_dataset_sha256"] != profile["source_dataset_sha256"]):
        raise ValueError(provider + ": incomplete or mismatched cohort")
    attempt_ids = {row["attempt_id"] for row in physical}
    if len(attempt_ids) != len(physical) or sum(row["physical_attempt_count"] for row in rows) != len(physical):
        raise ValueError(provider + ": physical call inventory mismatch")
    for row in rows:
        path = shard / row["artifact_path"]
        if path.is_symlink() or digest(path) != row["output_sha256"]:
            raise ValueError(provider + ": result artifact mismatch: " + row["track_id"])
    for row in physical:
        path = shard / row["physical_artifact_path"]
        if path.is_symlink() or digest(path) != row["output_sha256"]:
            raise ValueError(provider + ": physical artifact mismatch: " + row["attempt_id"])
    return {"provider": provider, "logical_results": len(rows), "physical_calls": len(physical),
            "request_successes": sum(bool(row["ok"]) for row in rows),
            "results_sha256": digest(shard / "results.jsonl"),
            "physical_attempts_sha256": digest(shard / "physical-attempts.jsonl"),
            "execution_sha256": digest(shard / "execution.json")}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--scorer", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=ROOT / "evaluation/webmainbench-full/current-report.json")
    parser.add_argument("--binary-source", type=Path,
                        help="Exact clean source checkout used to build a profile-bound candidate binary")
    args = parser.parse_args(argv)
    if args.output_root.exists():
        parser.error("Output root already exists; cohort evidence is immutable")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    declared = {"webmainbench-545-full-five-methods": ["moli", "lightpanda", "obscura", "chromium", "kitesurf"],
                "webmainbench-545-moli-fixed": ["moli"]}
    if (profile.get("providers") != declared.get(profile.get("dataset_id"))
            or profile.get("case_count") != 545
            or profile.get("runs_per_case") != 1 or profile.get("concurrency") != 1):
        parser.error("Not the complete declared WebMainBench method matrix")
    profile_sha = digest(args.profile)
    before = source_binding(args.source, args.scorer, profile)
    source_commit = profile.get('source_commit')
    verify_source_checkout(profile, args.binary_source)
    verify_declared_binary(profile)
    case_ids = {
        record["track_id"]
        for record in load_source(
            args.source, expected_sha256=profile["source_dataset_sha256"]
        )
    }
    args.output_root.mkdir(parents=True)
    (args.output_root / "preflight.json").write_text(
        json.dumps({"profile_sha256": profile_sha, **before}, indent=2) + "\n", encoding="utf-8")
    summaries = []
    for provider in profile["providers"]:
        print("Starting certified full-cohort method: " + provider, flush=True)
        command = [sys.executable, str(ROOT / "scripts/run_webmainbench_benchmark.py"),
                   "--provider", provider, "--dataset", str(args.source),
                   "--output", str(args.output_root / provider),
                   "--timeout", str(profile["timeout_seconds"]),
                   "--max-failure-retries", str(profile["max_failure_retries"])]
        command += [
            "--expected-dataset-sha256", profile["source_dataset_sha256"],
            "--official-scorer-commit", profile["official_scorer_commit"],
        ]
        if provider != "kitesurf":
            version = profile["versions"][provider].split()[-1]
            command += ["--expected-version", version]
        subprocess.run(command, cwd=ROOT, check=True)
        summaries.append(verify_shard(args.output_root / provider, provider, case_ids, profile))
        expected_binary = profile.get("binary_sha256")
        if expected_binary and json.loads((args.output_root / provider / "execution.json").read_text())[
                "implementation_identity"]["binary_sha256"] != expected_binary:
            raise ValueError("Measured binary differs from declared version")
        if source_binding(args.source, args.scorer, profile) != before or digest(args.profile) != profile_sha:
            raise ValueError("Source, scorer or profile changed during provider calls")
        verify_source_checkout(profile, args.binary_source)
    certificate = {"schema_version": 1, "kind": "certified_webmainbench_545_full_v1",
                   "profile_sha256": profile_sha, "preflight": before,
                   "postflight": source_binding(args.source, args.scorer, profile),
                   "binary_source_commit": source_commit,
                   "measured_binary_sha256": profile.get('binary_sha256'),
                   "providers": summaries}
    (args.output_root / "cohort-certificate.json").write_text(
        json.dumps(certificate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Certified complete WebMainBench cohort: " + str(args.output_root), flush=True)


def scorer_source_binding(scorer: Path) -> dict[str, str]:
    files = sorted((scorer / "webmainbench/metrics").glob("*.py"))
    if not files:
        raise ValueError("Pinned scorer metrics source is absent")
    return {str(path.relative_to(scorer)): digest(path) for path in files}



def main_score() -> None:
    from .webmainbench_scoring import verify_score

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--scorer", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=ROOT / "evaluation/webmainbench-full/current-report.json")
    args = parser.parse_args()
    if (args.cohort / "scoring-certificate.json").exists():
        parser.error("Scoring certificate already exists; refusing overwrite")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    cohort = json.loads((args.cohort / "cohort-certificate.json").read_text(encoding="utf-8"))
    if (cohort.get("kind") != "certified_webmainbench_545_full_v1"
            or cohort.get("profile_sha256") != digest(args.profile)):
        parser.error("Complete certified cohort is required")
    before = source_binding(args.source, args.scorer, profile)
    scorer_files = scorer_source_binding(args.scorer)
    if before != cohort["postflight"]:
        parser.error("Scoring input differs from the certified cohort")
    source_rows = read_jsonl(args.source)
    case_ids = {row["track_id"] for row in source_rows}
    if len(source_rows) != profile["case_count"] or len(case_ids) != len(source_rows):
        parser.error("Source case inventory differs from the declared cohort")
    receipts = []
    for provider in profile["providers"]:
        shard = args.cohort / provider
        print("Scoring " + provider, flush=True)
        with (shard / "scoring.log").open("w", encoding="utf-8") as stream:
            subprocess.run([sys.executable, "-m", "harness.web_fetch.webmainbench_scoring",
                            "--evidence", str(shard), "--dataset", str(args.source),
                            "--scorer-repo", str(args.scorer),
                            "--expected-dataset-sha256", profile["source_dataset_sha256"],
                            "--expected-scorer-commit", profile["official_scorer_commit"]],
                           cwd=ROOT, stdout=stream,
                           stderr=subprocess.STDOUT, check=True)
        receipts.append(verify_score(shard, provider, case_ids, args.scorer, profile))
        if source_binding(args.source, args.scorer, profile) != before or scorer_source_binding(args.scorer) != scorer_files:
            raise ValueError("Source or scorer changed during scoring")
        print(provider + " scored and verified", flush=True)
    certificate = {"schema_version": 1, "kind": "certified_webmainbench_scoring_v1",
                   "cohort_certificate_sha256": digest(args.cohort / "cohort-certificate.json"),
                   "source_binding": before, "scorer_source_sha256": scorer_files,
                   "providers": receipts}
    (args.cohort / "scoring-certificate.json").write_text(
        json.dumps(certificate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Certified complete scoring", flush=True)


if __name__ == "__main__":
    main()
