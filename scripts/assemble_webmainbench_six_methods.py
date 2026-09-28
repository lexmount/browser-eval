"""Join two independently certified full-scope runs for a six-version report."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_certified_webmainbench_full import digest, read_jsonl, verify_shard
from scripts.score_certified_webmainbench_full import verify_score
from harness.web_fetch.safety import contained_path

FIXED = "moli-fixed"


def require_unique_attempt_ids(rows: list[dict], label: str) -> None:
    attempt_ids = [row.get("attempt_id") for row in rows]
    if None in attempt_ids or len(set(attempt_ids)) != len(attempt_ids):
        raise ValueError(f"{label} attempt identity is not unique")


def assemble(baseline: Path, candidate: Path, output: Path, scorer: Path,
             fixed_profile_path: Path | None = None) -> None:
    if output.exists():
        raise ValueError("Composite evidence is immutable")
    base_profile_path = ROOT / "evaluation/webmainbench-full/current-report.json"
    fixed_profile_path = fixed_profile_path or (
        ROOT / "evaluation/webmainbench-full/moli-pr785-fixed-report.json"
    )
    base_profile = json.loads(base_profile_path.read_text())
    fixed_profile = json.loads(fixed_profile_path.read_text())
    base_cert = json.loads((baseline / "cohort-certificate.json").read_text())
    fixed_cert = json.loads((candidate / "cohort-certificate.json").read_text())
    score_cert = json.loads((candidate / "scoring-certificate.json").read_text())
    if (base_cert["profile_sha256"] != digest(base_profile_path)
            or fixed_cert["profile_sha256"] != digest(fixed_profile_path)
            or fixed_cert["preflight"] != fixed_cert["postflight"]
            or fixed_cert["preflight"] != base_cert["postflight"]
            or fixed_cert.get("binary_source_commit") != fixed_profile.get("source_commit")
            or fixed_cert.get("measured_binary_sha256") != fixed_profile.get("binary_sha256")
            or score_cert["cohort_certificate_sha256"] != digest(candidate / "cohort-certificate.json")
            or base_profile["source_dataset_sha256"] != fixed_profile["source_dataset_sha256"]):
        raise ValueError("Independent complete-run bindings differ")
    if any(path.is_symlink() for root in (baseline, candidate) for path in root.rglob("*")):
        raise ValueError("Symlinked evidence is forbidden")
    dataset = json.loads((baseline / "dataset.json").read_text())
    ids = {item["id"] for item in dataset["cases"]}
    shard = candidate / "moli"
    if verify_shard(shard, "moli", ids, fixed_profile) != fixed_cert["providers"][0]:
        raise ValueError("Fixed run differs from certificate")
    if verify_score(shard, "moli", ids, scorer, fixed_profile) != score_cert["providers"][0]:
        raise ValueError("Fixed scores differ from certificate")
    measured = read_jsonl(shard / "scored-results.jsonl")
    if len(measured) != 545 or {row["track_id"] for row in measured} != ids:
        raise ValueError("Fixed run is incomplete")
    shutil.copytree(baseline, output)
    def alias(row: dict) -> dict:
        row = copy.deepcopy(row)
        row["provider"] = FIXED
        row["attempt_id"] = row["attempt_id"].replace("moli__", FIXED + "__", 1)
        for key in ("artifact_path", "physical_artifact_path"):
            if key in row:
                row[key] = row[key].replace("artifacts/moli/", f"artifacts/{FIXED}/", 1)
        return row
    rows = [alias({**row, "case_id": row["track_id"]}) for row in measured]
    attempts = [alias(row) for row in read_jsonl(shard / "physical-attempts.jsonl")]
    if len(rows) != 545:
        raise ValueError("Fixed logical result matrix is incomplete")
    require_unique_attempt_ids(rows, "Fixed logical result")
    for row in measured:
        source = contained_path(shard, row["artifact_path"])
        if digest(source) != row["output_sha256"]:
            raise ValueError("Fixed output changed")
        target = contained_path(output, row["artifact_path"].replace("artifacts/moli/", f"artifacts/{FIXED}/", 1))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for row in read_jsonl(shard / "physical-attempts.jsonl"):
        source = contained_path(shard, row["physical_artifact_path"])
        if digest(source) != row["output_sha256"]:
            raise ValueError("Fixed physical output changed")
        target = contained_path(output, row["physical_artifact_path"].replace("artifacts/moli/", f"artifacts/{FIXED}/", 1))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    combined = read_jsonl(baseline / "results.jsonl") + rows
    if len(combined) != 3270:
        raise ValueError("Six-version result matrix is incomplete")
    require_unique_attempt_ids(combined, "Six-version logical result")
    (output / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in combined))
    physical = read_jsonl(baseline / "physical-attempts.jsonl") + attempts
    require_unique_attempt_ids(physical, "Six-version physical")
    (output / "physical-attempts.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in physical))
    summary = json.loads((baseline / "score-summaries.json").read_text())
    summary[FIXED] = {**json.loads((shard / "score-summary.json").read_text()), "provider": FIXED}
    (output / "score-summaries.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    execution = json.loads((baseline / "execution.json").read_text())
    fixed_execution = json.loads((shard / "execution.json").read_text())
    if fixed_execution["implementation_identity"]["binary_sha256"] != fixed_profile["binary_sha256"]:
        raise ValueError("Fixed binary changed")
    fixed_execution["provider"] = FIXED
    execution["batches"].append(fixed_execution)
    (output / "execution.json").write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n")
    dataset["coverage_plan"]["static_html"].append(FIXED)
    dataset["name"] = "WebMainBench-545 full · six Web Fetch versions"
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    shutil.copy2(candidate / "cohort-certificate.json", output / "fixed-cohort-certificate.json")
    shutil.copy2(candidate / "scoring-certificate.json", output / "fixed-scoring-certificate.json")
    shutil.copy2(fixed_profile_path, output / "moli-fixed-report.json")
    binding = {"schema_version": 1, "kind": "webmainbench_six_version_full_scope_binding",
               "baseline_profile_sha256": digest(base_profile_path),
               "baseline_certificate_sha256": digest(baseline / "cohort-certificate.json"),
               "fixed_profile_sha256": digest(fixed_profile_path),
               "fixed_certificate_sha256": digest(candidate / "cohort-certificate.json"),
               "fixed_scoring_certificate_sha256": digest(candidate / "scoring-certificate.json"),
               "fixed_scored_results_sha256": digest(shard / "scored-results.jsonl"),
               "fixed_binary_sha256": fixed_profile["binary_sha256"],
               "fixed_source_commit": fixed_profile["source_commit"],
               # This is the complete six-method matrix before TEDS recomputation.
               # correct_webmainbench_teds.py later preserves these exact bytes as
               # original-results.jsonl, which is what the build and release gates verify.
               "results_sha256": digest(output / "results.jsonl"),
               "logical_results": len(combined), "physical_calls": len(physical)}
    (output / "six-method-binding.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scorer", required=True, type=Path)
    parser.add_argument(
        "--fixed-profile",
        type=Path,
        default=ROOT / "evaluation/webmainbench-full/moli-pr785-fixed-report.json",
        help="Profile that certified the candidate cohort",
    )
    args = parser.parse_args()
    assemble(
        args.baseline,
        args.candidate,
        args.output,
        args.scorer,
        args.fixed_profile,
    )


if __name__ == "__main__":
    main()
