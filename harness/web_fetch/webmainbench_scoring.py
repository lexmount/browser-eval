"""Score immutable browser outputs with the pinned official WebMainBench metrics."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import subprocess
import sys
import types
from collections import defaultdict
from pathlib import Path
from threading import RLock

from lexbench_webfetch.paths import contained_path

from harness.record_io import load_jsonl
from harness.web_fetch.webmainbench_evidence import digest, read_jsonl

SCORER_COMMIT = "9d991bdc00c57b57521499494d96be85c31317ba"
DATASET_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"
METRIC_SOURCE_MANIFEST_SHA256 = "d1a503f7a475da2e118f008f9a63c49cba6fb2378b08de01fb2c3c4c1b96dc31"
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


_SCORER_IMPORT_LOCK = RLock()


def official_calculator(repo: Path, cache_dir: Path, expected_commit: str = SCORER_COMMIT):
    """Load the declared clean checkout without reusing another scorer's modules."""
    with _SCORER_IMPORT_LOCK:
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
        ).strip()
        if commit != expected_commit:
            raise ValueError(f"Official scorer commit mismatch: {commit}")
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"], text=True,
        )
        if status:
            raise ValueError("Official scorer requires a clean tracked checkout")
        previous = {name: value for name, value in sys.modules.items()
                    if name == "webmainbench" or name.startswith("webmainbench.")}
        for name in previous:
            del sys.modules[name]
        try:
            importlib.invalidate_caches()
            package = types.ModuleType("webmainbench")
            package.__path__ = [str(repo.resolve() / "webmainbench")]
            sys.modules["webmainbench"] = package
            calculator_type = importlib.import_module(
                "webmainbench.metrics.calculator"
            ).MetricCalculator
            return calculator_type({"use_llm": False, "cache_dir": str(cache_dir)})
        except BaseException:
            for name in list(sys.modules):
                if name == "webmainbench" or name.startswith("webmainbench."):
                    del sys.modules[name]
            sys.modules.update(previous)
            raise


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
            if not metric or metric.get("details", {}).get("availability") == "reference_not_applicable":
                continue
            totals[name] += metric["score"] if metric.get("success") else 0.0
            counts[name] += 1
    scores = {
        name: totals[name] / counts[name] if counts[name] else 0.0
        for name in CORE_METRICS
    }
    scores["overall"] = sum(scores[name] for name in CORE_METRICS) / len(CORE_METRICS)
    return {"scores": scores, "denominators": counts}


def zero(name: str, content_type: str) -> dict:
    return {"score": 0.0, "success": True,
            "details": {"content_type": content_type,
                        "prediction_source": "extraction_failure"}}


def revised_metrics(old: dict, scorer, predicted: str, reference: str,
                    meta: dict, ok: bool, reference_changed: bool = False) -> dict:
    """Recompute every affected metric with the bound corrected scorer.

    A failed provider call is represented by an empty prediction.  The scorer
    decides metric applicability from the frozen reference metadata, so an
    applicable failure remains a zero while an inapplicable metric remains
    unavailable.  Text edit is reused only when its implementation and
    reference are unchanged; corrected references are rescored.
    """
    candidate = predicted if ok else ""
    result = {}
    if ok and not reference_changed and old.get("text_edit", {}).get("success"):
        result["text_edit"] = copy.deepcopy(old["text_edit"])
    else:
        result["text_edit"] = metric_dict(
            scorer.metrics["text_edit"].calculate(candidate, reference)
        )
    for name in ("code_edit", "formula_edit"):
        result[name] = metric_dict(scorer.metrics[name].calculate(
            candidate,
            reference,
            predicted_content_list=None,
            groundtruth_content_list=None,
            groundtruth_feature_scope=meta,
        ))
    table_edit = scorer.metrics["table_edit"].calculate(
        candidate,
        reference,
        predicted_content_list=None,
        groundtruth_content_list=None,
        groundtruth_feature_scope=meta,
    )
    result["table_edit"] = metric_dict(table_edit)
    result["table_TEDS"] = metric_dict(scorer.metrics["table_TEDS"].calculate(
        candidate,
        reference,
        predicted_content_list=None,
        groundtruth_content_list=None,
        table_edit_result=table_edit,
        groundtruth_feature_scope=meta,
    ))
    applicable = {
        "code_edit": bool(meta.get("code")),
        "formula_edit": bool(meta.get("equation")),
        "table_edit": bool(meta.get("table")),
        "table_TEDS": bool(meta.get("table")),
    }
    for name, is_applicable in applicable.items():
        availability = result[name].get("details", {}).get("availability")
        if (is_applicable and not result[name].get("success")
                and availability != "reference_not_applicable"):
            error = result[name].get("error")
            result[name] = zero(name, name.removesuffix("_edit").replace("_TEDS", ""))
            result[name]["details"].update({
                "prediction_source": "metric_error",
                "scorer_error": error,
            })
    successful = {name: value["score"] for name, value in result.items()
                  if value.get("success")}
    result["overall"] = {
        "score": sum(successful.values()) / len(successful) if successful else 0.0,
        "success": bool(successful),
        "details": {
            "source": "average_of_all_metrics",
            "successful_metrics": len(successful),
            "failed_metrics": len(CORE_METRICS) - len(successful),
            "individual_scores": successful,
        },
    }
    return result


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
    logical = load_jsonl(args.evidence / "results.jsonl", skip_blank=True)
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
        if digest(contained_path(scorer, relative, reject_symlinks=True)) != expected:
            raise ValueError(provider + ": scorer source changed: " + relative)
    return {"provider": provider, "scored_results_sha256": digest(shard / "scored-results.jsonl"),
            "score_summary_sha256": digest(shard / "score-summary.json"),
            "logical_results": len(scored)}


if __name__ == "__main__":
    main()
