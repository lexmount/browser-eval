"""Rescore a certified WebMainBench case correction without new provider calls."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.record_io import load_jsonl
from harness.web_fetch.safety import contained_path
from scripts.correct_webmainbench_references import calculator
from scripts.correct_webmainbench_scoring import revised_metrics, text_digest


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(source: Path, corrected_dataset: Path, scorer_repo: Path,
          scorer_commit: str, output: Path) -> dict:
    if output.exists():
        raise ValueError("Refusing to replace case-correction evidence: " + str(output))
    certificate = json.loads((source / "certificate.json").read_text(encoding="utf-8"))
    if (certificate.get("kind") != "webmainbench_origin_correction_v1"
            or certificate.get("preflight") != certificate.get("postflight")
            or certificate.get("results_sha256") != digest(source / "results.jsonl")
            or certificate.get("physical_sha256") != digest(source / "physical-attempts.jsonl")):
        raise ValueError("Invalid source case-correction certificate")
    cases = {
        row["track_id"]: row
        for row in iter(load_jsonl(corrected_dataset))
    }
    case_id = certificate["preflight"]["case_id"]
    case = cases[case_id]
    rows = load_jsonl(source / "results.jsonl")
    if ({row["provider"] for row in rows} != set(certificate["preflight"]["providers"])
            or any(row["case_id"] != case_id for row in rows)):
        raise ValueError("Case-correction result scope mismatch")
    with tempfile.TemporaryDirectory(prefix="webmainbench-case-rescore-") as cache:
        scorer = calculator(scorer_repo, scorer_commit, Path(cache))
        revised = []
        for original in rows:
            row = copy.deepcopy(original)
            artifact = contained_path(source, row["artifact_path"])
            if digest(artifact) != row["output_sha256"]:
                raise ValueError("Case-correction provider artifact changed")
            predicted = artifact.read_text(encoding="utf-8") if row.get("ok") else ""
            row["pre_scoring_correction_metrics"] = copy.deepcopy(row["metrics"])
            row["metrics"] = revised_metrics(
                row["metrics"], scorer, predicted, case["groundtruth_content"],
                case["meta"], bool(row.get("ok")), False,
            )
            row["meta"] = copy.deepcopy(case["meta"])
            row["reference_sha256"] = text_digest(case["groundtruth_content"])
            revised.append(row)
    shutil.copytree(source, output)
    result_path = output / "results.jsonl"
    result_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in revised), encoding="utf-8"
    )
    certificate["results_sha256"] = digest(result_path)
    (output / "certificate.json").write_text(
        json.dumps(certificate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    receipt = {
        "schema_version": 1,
        "kind": "webmainbench_case_correction_scoring_revision",
        "provider_calls": 0,
        "logical_results": len(revised),
        "case_id": case_id,
        "providers": certificate["preflight"]["providers"],
        "scorer_repository": "https://github.com/lexmount/WebMainBench",
        "scorer_commit": scorer_commit,
        "corrected_dataset_sha256": digest(corrected_dataset),
        "input_results_sha256": digest(source / "results.jsonl"),
        "output_results_sha256": digest(result_path),
    }
    (output / "scoring-correction.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--corrected-dataset", required=True, type=Path)
    parser.add_argument("--scorer-repo", required=True, type=Path)
    parser.add_argument("--scorer-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(apply(args.source, args.corrected_dataset, args.scorer_repo,
                           args.scorer_commit, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
