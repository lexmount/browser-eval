"""Run the predeclared complete WebMainBench cohort with source and result checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from harness.web_fetch.config import load_provider_config, load_runtime_env
from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.web_fetch.webmainbench_evidence import digest, read_jsonl, require_unique_attempt_ids
from harness.web_fetch.webmainbench_runner import PROVIDERS, load_source


def _read_json(root: Path, relative: str) -> dict:
    value = json.loads(contained_path(root, relative, reject_symlinks=True).read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Expected an evidence object: " + relative)
    return value


def _read_profile(path: Path) -> tuple[dict, str]:
    frozen = contained_path(path.parent, path.name, reject_symlinks=True).read_bytes()
    profile = json.loads(frozen)
    if not isinstance(profile, dict):
        raise ValueError("Profile must be an object")
    methods = profile.get("providers")
    versions = profile.get("versions", {})
    timeout = profile.get("timeout_seconds")
    if (profile.get("schema_version") != 1
            or not isinstance(methods, list) or not methods
            or any(not isinstance(name, str) or name not in PROVIDERS for name in methods)
            or len(set(methods)) != len(methods)
            or type(profile.get("case_count")) is not int or profile["case_count"] != 545
            or type(profile.get("runs_per_case")) is not int or profile["runs_per_case"] != 1
            or type(profile.get("concurrency")) is not int or profile["concurrency"] != 1
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or type(profile.get("max_failure_retries")) is not int or profile["max_failure_retries"] < 0
            or not isinstance(versions, dict)
            or any(not isinstance(versions.get(name), str) or not versions[name].strip()
                   for name in methods if name != "kitesurf")
            or (profile.get("binary_sha256") and len(methods) != 1)):
        raise ValueError("Invalid complete WebMainBench profile or registered method scope")
    for name in methods:
        safe_identifier(name)
    return profile, hashlib.sha256(frozen).hexdigest()


def _unchanged(files: dict[Path, str]) -> None:
    if any(path.is_symlink() or digest(path) != expected for path, expected in files.items()):
        raise ValueError("Declared inputs changed during execution")


def source_binding(source: Path, scorer: Path, profile: dict) -> dict:
    if source.is_symlink() or scorer.is_symlink():
        raise ValueError("Symlinked source or scorer is forbidden")
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
    dirty = subprocess.check_output(
        ["git", "-C", str(scorer), "status", "--porcelain", "--untracked-files=no"], text=True)
    if dirty:
        raise ValueError("Official scorer requires a clean tracked checkout")
    return {"source_sha256": digest(source), "case_ids_sha256": case_digest,
            "scorer_commit": commit, "case_count": len(records)}


def verify_declared_binary(profile: dict, binary: str) -> None:
    if not profile.get('binary_sha256'):
        return
    executable = shutil.which(binary)
    if executable is None or digest(Path(executable)) != profile['binary_sha256']:
        raise ValueError('Declared browser binary differs before provider calls')


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
    rows = read_jsonl(contained_path(shard, "results.jsonl", reject_symlinks=True))
    physical = read_jsonl(contained_path(shard, "physical-attempts.jsonl", reject_symlinks=True))
    execution = _read_json(shard, "execution.json")
    dataset = _read_json(shard, "dataset.json")
    if (len(rows) != profile["case_count"] or {row["track_id"] for row in rows} != case_ids
            or len({row["track_id"] for row in rows}) != len(rows)
            or any(row.get("provider") != provider for row in rows + physical)
            or {row["track_id"] for row in physical} != case_ids
            or dataset["case_count"] != profile["case_count"]
            or len(dataset["case_ids"]) != profile["case_count"]
            or set(dataset["case_ids"]) != case_ids
            or execution["case_count"] != profile["case_count"]
            or execution["provider"] != provider
            or execution["source_dataset_sha256"] != profile["source_dataset_sha256"]):
        raise ValueError(provider + ": incomplete or mismatched cohort")
    attempt_ids = {row["attempt_id"] for row in physical}
    if len(attempt_ids) != len(physical) or sum(row["physical_attempt_count"] for row in rows) != len(physical):
        raise ValueError(provider + ": physical call inventory mismatch")
    require_unique_attempt_ids(rows, "Logical")
    require_unique_attempt_ids(physical, "Physical")
    for row in rows:
        path = contained_path(shard, row["artifact_path"], reject_symlinks=True)
        if path.is_symlink() or digest(path) != row["output_sha256"]:
            raise ValueError(provider + ": result artifact mismatch: " + row["track_id"])
    for row in physical:
        path = contained_path(shard, row["physical_artifact_path"], reject_symlinks=True)
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
    parser.add_argument("--profile", type=Path, default=Path("evaluation/webmainbench-full/current-report.json"))
    parser.add_argument("--config", type=Path, default=Path("configs/providers.headless.json"))
    parser.add_argument("--env-file", type=Path, help="Explicit local evaluation credentials file")
    parser.add_argument("--binary-source", type=Path,
                        help="Exact clean source checkout used to build a profile-bound candidate binary")
    args = parser.parse_args(argv)
    for name in ("output_root", "source", "scorer", "profile", "binary_source", "config", "env_file"):
        if (path := getattr(args, name)) is not None:
            setattr(args, name, path.absolute())
    if args.output_root.exists() or args.output_root.is_symlink():
        parser.error("Output root already exists; cohort evidence is immutable")
    profile, profile_sha = _read_profile(args.profile)
    bound = {args.profile: profile_sha}
    for path in (args.config, args.env_file):
        if path is not None:
            if path.is_symlink():
                raise ValueError("Symlinked runtime input is forbidden")
            bound[path] = digest(path)
    load_runtime_env(args.env_file)
    settings = load_provider_config(args.config)
    if any(not isinstance(settings.get(name), dict) for name in profile["providers"]):
        raise ValueError("Runtime configuration is missing a declared method")
    before = source_binding(args.source, args.scorer, profile)
    source_commit = profile.get('source_commit')
    verify_source_checkout(profile, args.binary_source)
    if profile.get("binary_sha256"):
        candidate = settings[profile["providers"][0]]
        binary = candidate.get("binary") or candidate.get("engine")
        if not isinstance(binary, str) or not binary:
            raise ValueError("Profile-bound binary requires a configured executable")
        verify_declared_binary(profile, binary)
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
        _unchanged(bound)
        print("Starting certified full-cohort method: " + provider, flush=True)
        command = [sys.executable, "-m", "harness.web_fetch.webmainbench_runner",
                   "--provider", provider, "--dataset", str(args.source),
                   "--config", str(args.config),
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
        if args.env_file is not None:
            command += ["--env-file", str(args.env_file)]
        subprocess.run(command, check=True)
        summaries.append(verify_shard(args.output_root / provider, provider, case_ids, profile))
        expected_binary = profile.get("binary_sha256")
        if expected_binary and json.loads((args.output_root / provider / "execution.json").read_text())[
                "implementation_identity"]["binary_sha256"] != expected_binary:
            raise ValueError("Measured binary differs from declared version")
        _unchanged(bound)
        if source_binding(args.source, args.scorer, profile) != before:
            raise ValueError("Source, scorer or profile changed during provider calls")
        verify_source_checkout(profile, args.binary_source)
    for expected in summaries:
        provider = expected["provider"]
        if verify_shard(args.output_root / provider, provider, case_ids, profile) != expected:
            raise ValueError(provider + ": measured evidence changed before certification")
    certificate = {"schema_version": 1, "kind": "certified_webmainbench_545_full_v1",
                   "profile_sha256": profile_sha, "preflight": before,
                   "postflight": source_binding(args.source, args.scorer, profile),
                   "binary_source_commit": source_commit,
                   "measured_binary_sha256": profile.get('binary_sha256'),
                   "provider_config_sha256": bound[args.config],
                   "providers": summaries}
    _unchanged(bound)
    with (args.output_root / "cohort-certificate.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(certificate, ensure_ascii=False, indent=2) + "\n")
    print("Certified complete WebMainBench cohort: " + str(args.output_root), flush=True)


def scorer_source_binding(scorer: Path) -> dict[str, str]:
    files = sorted((scorer / "webmainbench/metrics").glob("*.py"))
    if not files:
        raise ValueError("Pinned scorer metrics source is absent")
    return {str(path.relative_to(scorer)): digest(contained_path(
        scorer, path.relative_to(scorer), reject_symlinks=True)) for path in files}



def main_score(argv: list[str] | None = None) -> None:
    from .webmainbench_scoring import verify_score

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--scorer", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path("evaluation/webmainbench-full/current-report.json"))
    args = parser.parse_args(argv)
    for name in ("cohort", "source", "scorer", "profile"):
        setattr(args, name, getattr(args, name).absolute())
    score_certificate = args.cohort / "scoring-certificate.json"
    if score_certificate.exists() or score_certificate.is_symlink():
        parser.error("Scoring certificate already exists; refusing overwrite")
    profile, profile_sha = _read_profile(args.profile)
    cohort_path = contained_path(args.cohort, "cohort-certificate.json", reject_symlinks=True)
    frozen_cohort = cohort_path.read_bytes()
    cohort = json.loads(frozen_cohort)
    bound = {args.profile: profile_sha, cohort_path: hashlib.sha256(frozen_cohort).hexdigest()}
    if (cohort.get("kind") != "certified_webmainbench_545_full_v1"
            or cohort.get("profile_sha256") != profile_sha
            or cohort.get("preflight") != cohort.get("postflight")
            or [row["provider"] for row in cohort.get("providers", [])] != profile["providers"]):
        parser.error("Complete certified cohort is required")
    before = source_binding(args.source, args.scorer, profile)
    scorer_files = scorer_source_binding(args.scorer)
    if before != cohort["postflight"]:
        parser.error("Scoring input differs from the certified cohort")
    source_rows = read_jsonl(args.source)
    case_ids = {row["track_id"] for row in source_rows}
    if len(source_rows) != profile["case_count"] or len(case_ids) != len(source_rows):
        parser.error("Source case inventory differs from the declared cohort")

    def verify_measurements() -> None:
        _unchanged(bound)
        for expected in cohort["providers"]:
            provider = expected["provider"]
            shard = contained_path(args.cohort, provider, reject_symlinks=True)
            if verify_shard(shard, provider, case_ids, profile) != expected:
                raise ValueError(provider + ": measured shard differs from its cohort certificate")

    verify_measurements()
    receipts = []
    for provider in profile["providers"]:
        shard = contained_path(args.cohort, provider, reject_symlinks=True)
        print("Scoring " + provider, flush=True)
        with contained_path(shard, "scoring.log", reject_symlinks=True).open("w", encoding="utf-8") as stream:
            subprocess.run([sys.executable, "-m", "harness.web_fetch.webmainbench_scoring",
                            "--evidence", str(shard), "--dataset", str(args.source),
                            "--scorer-repo", str(args.scorer),
                            "--expected-dataset-sha256", profile["source_dataset_sha256"],
                            "--expected-scorer-commit", profile["official_scorer_commit"]],
                           stdout=stream,
                           stderr=subprocess.STDOUT, check=True)
        receipts.append(verify_score(shard, provider, case_ids, args.scorer, profile))
        if _read_json(shard, "score-summary.json")["scorer"]["metric_source_sha256"] != scorer_files:
            raise ValueError(provider + ": incomplete scorer source binding")
        verify_measurements()
        if source_binding(args.source, args.scorer, profile) != before or scorer_source_binding(args.scorer) != scorer_files:
            raise ValueError("Source or scorer changed during scoring")
        print(provider + " scored and verified", flush=True)
    certificate = {"schema_version": 1, "kind": "certified_webmainbench_scoring_v1",
                   "cohort_certificate_sha256": bound[cohort_path],
                   "source_binding": before, "scorer_source_sha256": scorer_files,
                   "providers": receipts}
    verify_measurements()
    with score_certificate.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(certificate, ensure_ascii=False, indent=2) + "\n")
    print("Certified complete scoring", flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["score"]:
        main_score(sys.argv[2:])
    else:
        main()
