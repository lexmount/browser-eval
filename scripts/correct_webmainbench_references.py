"""Apply hash-bound reference corrections without calling any provider."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_diagnostics import validate_teds_revision
from harness.web_fetch.webmainbench_scoring import aggregate, metric_dict


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def copy_contained_artifact(source_root: Path, source_relative: str,
                            output_root: Path, output_relative: str) -> Path:
    """Copy a declared artifact while keeping both paths inside their roots."""
    source = contained_path(source_root, source_relative)
    destination = contained_path(output_root, output_relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def calculator(repo: Path, expected_commit: str, cache: Path):
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != expected_commit:
        raise ValueError(f"Corrected scorer commit mismatch: {commit}")
    for name in list(sys.modules):
        if name == "webmainbench" or name.startswith("webmainbench."):
            del sys.modules[name]
    package = types.ModuleType("webmainbench")
    package.__path__ = [str(repo / "webmainbench")]
    sys.modules["webmainbench"] = package
    calculator_type = importlib.import_module(
        "webmainbench.metrics.calculator"
    ).MetricCalculator
    return calculator_type({"use_llm": False, "cache_dir": str(cache)})


def category(case: dict, axis: str) -> str:
    value = case["meta"].get(axis)
    if isinstance(value, list):
        value = "+".join(sorted(value)) if value else "none"
    return str(value if value not in (None, "") else "unavailable")


def apply(source: Path, corrected_dataset: Path, manifest_path: Path,
          scorer_repo: Path, scorer_commit: str, output: Path) -> dict:
    if output.exists():
        raise ValueError("Reference correction output already exists")
    validate_teds_revision(source)
    source_dataset = json.loads((source / "dataset.json").read_text(encoding="utf-8"))
    source_cases = {case["id"]: case for case in source_dataset["cases"]}
    corrected_rows = {row["track_id"]: row for row in read_rows(corrected_dataset)}
    if len(source_cases) != 545 or set(corrected_rows) != set(source_cases):
        raise ValueError("Corrected dataset population differs")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported reference correction manifest")
    corrections = {item["track_id"]: item for item in manifest["corrections"]}
    if len(corrections) != len(manifest["corrections"]):
        raise ValueError("Duplicate reference correction track ID")

    changed = set()
    for case_id, case in source_cases.items():
        row = corrected_rows[case_id]
        if row["url"] != case["url"] or text_digest(row["html"]) != case["input_html_sha256"]:
            raise ValueError("Corrected dataset changed frozen input: " + case_id)
        reference = row["groundtruth_content"]
        new_hash = text_digest(reference)
        if new_hash != case["reference_sha256"]:
            changed.add(case_id)
            correction = corrections.get(case_id)
            if not correction:
                raise ValueError("Undeclared reference correction: " + case_id)
            if (case["reference_sha256"] != correction["original_groundtruth_sha256"]
                    or new_hash != correction["replacement_sha256"]
                    or case["input_html_sha256"] != correction["html_sha256"]
                    or row["url"] != correction["url"]):
                raise ValueError("Reference correction binding mismatch: " + case_id)
            replacement = manifest_path.parent / correction["replacement_file"]
            if digest(replacement) != new_hash or replacement.read_text(encoding="utf-8") != reference:
                raise ValueError("Reference replacement file mismatch: " + case_id)
    if changed != set(corrections):
        raise ValueError("Correction manifest scope differs from changed references")

    records = read_rows(source / "results.jsonl")
    providers = source_dataset["coverage_plan"]["static_html"]
    expected = {(provider, case_id) for provider in providers for case_id in source_cases}
    if len(records) != len(expected) or {(r["provider"], r["case_id"]) for r in records} != expected:
        raise ValueError("Reference correction requires the complete method-case matrix")
    for row in records:
        if row["reference_sha256"] != source_cases[row["case_id"]]["reference_sha256"]:
            raise ValueError("Result reference binding mismatch: " + row["attempt_id"])
        artifact = contained_path(source, row["artifact_path"])
        if digest(artifact) != row["output_sha256"]:
            raise ValueError("Provider artifact changed: " + row["attempt_id"])

    with tempfile.TemporaryDirectory(prefix="webmainbench-reference-cache-") as cache:
        scorer = calculator(scorer_repo, scorer_commit, Path(cache))
        revised = []
        for original in records:
            row = copy.deepcopy(original)
            if row["case_id"] in changed:
                reference = corrected_rows[row["case_id"]]["groundtruth_content"]
                row["pre_reference_correction_metrics"] = copy.deepcopy(row["metrics"])
                if row.get("ok"):
                    artifact = contained_path(source, row["artifact_path"])
                    calculated = scorer.calculate_all(
                        predicted_content=artifact.read_text(encoding="utf-8"),
                        groundtruth_content=reference,
                        predicted_content_list=None,
                        groundtruth_content_list=None,
                    )
                    row["metrics"] = {name: metric_dict(value) for name, value in calculated.items()}
                else:
                    row["metrics"] = {}
                row["reference_sha256"] = text_digest(reference)
            revised.append(row)

    summaries = json.loads((source / "score-summaries.json").read_text(encoding="utf-8"))
    for provider in providers:
        provider_rows = [row for row in revised if row["provider"] == provider]
        summary = summaries[provider]
        summary["pre_reference_correction_aggregate"] = copy.deepcopy(summary["official_aggregate"])
        summary["official_aggregate"] = aggregate(provider_rows)
        for axis, groups in summary["dimensions"].items():
            for value in groups:
                groups[value] = aggregate([
                    row for row in provider_rows
                    if category(source_cases[row["case_id"]], axis) == value
                ])
        summary["scorer"]["repository"] = "https://github.com/lexmount/WebMainBench"
        summary["scorer"]["commit"] = scorer_commit

    shutil.copytree(source, output)
    shutil.copy2(source / "results.jsonl", output / "pre-reference-correction-results.jsonl")
    shutil.copy2(source / "dataset.json", output / "pre-reference-correction-dataset.json")
    results_path = output / "results.jsonl"
    results_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in revised),
        encoding="utf-8",
    )
    for case_id in changed:
        reference = corrected_rows[case_id]["groundtruth_content"]
        contained_path(output / "references", f"{case_id}.md").write_text(
            reference, encoding="utf-8"
        )
        case = source_cases[case_id]
        case["reference_sha256"] = text_digest(reference)
        case["reference_chars"] = len(reference)
    source_dataset["source_dataset_sha256"] = digest(corrected_dataset)
    (output / "dataset.json").write_text(
        json.dumps(source_dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "score-summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    revision_source = output / "reference-correction-source"
    revision_source.mkdir()
    shutil.copy2(manifest_path, revision_source / manifest_path.name)
    for correction in corrections.values():
        copy_contained_artifact(
            manifest_path.parent,
            correction["replacement_file"],
            revision_source,
            correction["replacement_file"],
        )
    receipt = {
        "schema_version": 1,
        "kind": "webmainbench_reference_correction_v1",
        "provider_calls": 0,
        "scorer_repository": "https://github.com/lexmount/WebMainBench",
        "scorer_commit": scorer_commit,
        "manifest_sha256": digest(manifest_path),
        "source_dataset_sha256": source_dataset["source_dataset_sha256"],
        "prior_evidence_dataset_sha256": digest(source / "dataset.json"),
        "output_evidence_dataset_sha256": digest(output / "dataset.json"),
        "input_results_sha256": digest(source / "results.jsonl"),
        "output_results_sha256": digest(results_path),
        "logical_results": len(revised),
        "affected_logical_results": len(changed) * len(providers),
        "providers": providers,
        "corrections": [
            {
                "track_id": case_id,
                "original_reference_sha256": corrections[case_id]["original_groundtruth_sha256"],
                "replacement_reference_sha256": corrections[case_id]["replacement_sha256"],
            }
            for case_id in sorted(changed)
        ],
    }
    (output / "reference-correction.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--corrected-dataset", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--scorer-repo", required=True, type=Path)
    parser.add_argument("--scorer-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = apply(args.source, args.corrected_dataset, args.manifest,
                    args.scorer_repo, args.scorer_commit, args.output)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
