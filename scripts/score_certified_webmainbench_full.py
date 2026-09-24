"""Score every provider in the certified complete WebMainBench cohort."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_certified_webmainbench_full import digest, read_jsonl, source_binding
from scripts.score_webmainbench_results import aggregate


def verify_score(shard: Path, provider: str, case_ids: set[str], scorer: Path, profile: dict) -> dict:
    scored = read_jsonl(shard / "scored-results.jsonl")
    measured = read_jsonl(shard / "results.jsonl")
    summary = json.loads((shard / "score-summary.json").read_text(encoding="utf-8"))
    if (len(scored) != profile["case_count"] or len(measured) != len(scored)
            or {row["track_id"] for row in scored} != case_ids
            or len({row["track_id"] for row in scored}) != len(scored)
            or any(row["provider"] != provider for row in scored)
            or [{key: value for key, value in row.items() if key != "metrics"} for row in scored] != measured
            or summary.get("provider") != provider or summary.get("case_count") != len(scored)
            or summary.get("scorer", {}).get("commit") != profile["official_scorer_commit"]
            or summary.get("scorer", {}).get("metric_config") != profile["metric_config"]
            or summary["official_aggregate"] != aggregate(scored)):
        raise ValueError(provider + ": scored evidence is incomplete or differs from measured calls")
    for relative, expected in summary["scorer"]["metric_source_sha256"].items():
        if digest(scorer / relative) != expected:
            raise ValueError(provider + ": scorer source changed: " + relative)
    return {"provider": provider, "scored_results_sha256": digest(shard / "scored-results.jsonl"),
            "score_summary_sha256": digest(shard / "score-summary.json"),
            "logical_results": len(scored)}


def scorer_source_binding(scorer: Path) -> dict[str, str]:
    files = sorted((scorer / "webmainbench/metrics").glob("*.py"))
    if not files:
        raise ValueError("Pinned scorer metrics source is absent")
    return {str(path.relative_to(scorer)): digest(path) for path in files}


def main() -> None:
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
            subprocess.run([sys.executable, str(ROOT / "scripts/score_webmainbench_results.py"),
                            "--evidence", str(shard), "--dataset", str(args.source),
                            "--scorer-repo", str(args.scorer)], cwd=ROOT, stdout=stream,
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
