"""Score immutable browser outputs with the pinned official WebMainBench metrics."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
import types
from collections import defaultdict
from pathlib import Path


SCORER_COMMIT = "9d991bdc00c57b57521499494d96be85c31317ba"
DATASET_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"
CORE_METRICS = ("text_edit", "code_edit", "table_edit", "table_TEDS", "formula_edit")


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_records(path: Path, expected_sha256: str = DATASET_SHA256) -> dict[str, dict]:
    if sha256_path(path) != expected_sha256:
        raise ValueError("WebMainBench dataset hash mismatch")
    records = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                record = json.loads(line)
                records[record["track_id"]] = record
    if len(records) != 545:
        raise ValueError(f"Expected 545 source records, found {len(records)}")
    return records


def official_calculator(
    repo: Path, cache_dir: Path, expected_commit: str = SCORER_COMMIT
):
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if commit != expected_commit:
        raise ValueError(f"Official scorer commit mismatch: {commit}")
    package = types.ModuleType("webmainbench")
    package.__path__ = [str(repo / "webmainbench")]
    sys.modules["webmainbench"] = package
    calculator_type = importlib.import_module(
        "webmainbench.metrics.calculator"
    ).MetricCalculator
    return calculator_type({"use_llm": False, "cache_dir": str(cache_dir)})


def metric_dict(value) -> dict:
    return {
        "score": value.score,
        "success": value.success,
        "details": value.details,
        **({"error": value.error_message} if not value.success else {}),
    }


def aggregate(rows: list[dict]) -> dict:
    totals = {name: 0.0 for name in CORE_METRICS}
    counts = {name: 0 for name in CORE_METRICS}
    for row in rows:
        for name in CORE_METRICS:
            metric = row.get("metrics", {}).get(name, {})
            if metric.get("success"):
                totals[name] += metric["score"]
                counts[name] += 1
    scores = {
        name: totals[name] / counts[name] if counts[name] else 0.0
        for name in CORE_METRICS
    }
    scores["overall"] = sum(scores[name] for name in CORE_METRICS) / len(CORE_METRICS)
    return {"scores": scores, "denominators": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--scorer-repo", type=Path, required=True)
    parser.add_argument("--revision", default="", help="Suffix for an explicit scoring correction")
    parser.add_argument("--expected-dataset-sha256", default=DATASET_SHA256)
    parser.add_argument("--expected-scorer-commit", default=SCORER_COMMIT)
    args = parser.parse_args()
    suffix = f"-{args.revision}" if args.revision else ""
    output = args.evidence / f"scored-results{suffix}.jsonl"
    summary_path = args.evidence / f"score-summary{suffix}.json"
    if output.exists() or summary_path.exists():
        raise ValueError("Scored evidence exists; refusing to overwrite")

    source = load_records(args.dataset, args.expected_dataset_sha256)
    logical = [json.loads(line) for line in (args.evidence / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({row["track_id"] for row in logical}) != len(logical):
        raise ValueError("Logical results contain duplicate track IDs")
    calculator = official_calculator(
        args.scorer_repo,
        args.evidence / "official-scorer-cache",
        args.expected_scorer_commit,
    )
    scored = []
    with output.open("w", encoding="utf-8") as stream:
        for index, row in enumerate(logical, 1):
            reference = source[row["track_id"]]["groundtruth_content"]
            artifact = args.evidence / row["artifact_path"]
            if sha256_path(artifact) != row["output_sha256"]:
                raise ValueError(f"Artifact hash mismatch: {row['track_id']}")
            if row["ok"]:
                predicted = artifact.read_text(encoding="utf-8")
                calculated = calculator.calculate_all(
                    predicted_content=predicted,
                    groundtruth_content=reference,
                    predicted_content_list=None,
                    groundtruth_content_list=None,
                )
                metrics = {name: metric_dict(value) for name, value in calculated.items()}
            else:
                metrics = {}
            result = {**row, "metrics": metrics}
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            scored.append(result)
            print(f"[{index}/{len(logical)}] scored {row['provider']} {row['track_id']}", flush=True)

    dimensions: dict[str, dict[str, list[dict]]] = {
        axis: defaultdict(list) for axis in ("language", "level", "table", "code", "equation")
    }
    for row in scored:
        meta = row["meta"]
        for axis in dimensions:
            value = meta.get(axis)
            if isinstance(value, list):
                value = "+".join(sorted(value)) if value else "none"
            dimensions[axis][str(value if value not in (None, "") else "unavailable")].append(row)
    scorer_files = sorted((args.scorer_repo / "webmainbench/metrics").glob("*.py"))
    summary = {
        "schema_version": 1,
        "provider": scored[0]["provider"] if scored else None,
        "case_count": len(scored),
        "request_successes": sum(bool(row["ok"]) for row in scored),
        "request_success_rate": sum(bool(row["ok"]) for row in scored) / len(scored),
        "official_aggregate": aggregate(scored),
        "dimensions": {
            axis: {value: aggregate(rows) for value, rows in sorted(groups.items())}
            for axis, groups in dimensions.items()
        },
        "scorer": {
            "repository": "https://github.com/opendatalab/WebMainBench",
            "commit": args.expected_scorer_commit,
            "metric_config": {"use_llm": False},
            "aggregation": "official arithmetic mean of five independently aggregated metrics",
            "metric_source_sha256": {
                str(path.relative_to(args.scorer_repo)): sha256_path(path) for path in scorer_files
            },
        },
        "supersedes": ["scored-results.jsonl", "score-summary.json"] if args.revision else [],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
