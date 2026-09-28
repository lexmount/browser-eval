"""Apply reviewed WebMainBench dataset and scoring corrections without provider calls."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_diagnostics import validate_teds_revision
from scripts.correct_webmainbench_references import calculator, category, read_rows
from scripts.score_webmainbench_results import CORE_METRICS, aggregate, metric_dict


REGISTRY = ROOT / "evaluation/webmainbench-full/scoring-regressions.json"
CONTROL_IDS = (
    "prediction-only-code-does-not-expand-population",
    "missing-reference-scoped-code-scores-zero",
    "raw-html-pre-is-scorable-code",
    "layout-indented-prose-is-not-code",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text_digest(raw)


def unavailable(name: str, reason: str) -> dict:
    return {"score": 0.0, "success": False, "details": {"availability": "reference_defined"},
            "error": reason}


def zero(name: str, content_type: str) -> dict:
    return {"score": 0.0, "success": True,
            "details": {"content_type": content_type,
                        "prediction_source": "extraction_failure"}}


def validate_controls(scorer) -> None:
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in document["cases"]}
    if document.get("schema_version") != 1 or set(rows) != set(CONTROL_IDS):
        raise ValueError("WebMainBench scoring regression registry changed")
    modes = {}
    for identifier in CONTROL_IDS:
        row = rows[identifier]
        result = scorer.metrics["code_edit"].calculate(
            row["predicted"], row["reference"],
            groundtruth_feature_scope=row["meta"],
        )
        if result.success != row["expected_success"]:
            raise ValueError("Scoring regression success mismatch: " + identifier)
        if "expected_score" in row and result.score != row["expected_score"]:
            raise ValueError("Scoring regression score mismatch: " + identifier)
        modes.setdefault(row["root_cause"], set()).add(row["failure_mode"])
    if any(value != {"false-positive", "false-negative"} for value in modes.values()):
        raise ValueError("Each WebMainBench scoring root cause requires paired controls")


def revised_metrics(old: dict, scorer, predicted: str, reference: str,
                    meta: dict, ok: bool, reference_changed: bool = False) -> dict:
    result = {}
    if not ok:
        result["text_edit"] = zero("text_edit", "text")
    elif not reference_changed and old.get("text_edit", {}).get("success"):
        result["text_edit"] = copy.deepcopy(old["text_edit"])
    else:
        text = scorer.metrics["text_edit"].calculate(predicted, reference)
        result["text_edit"] = metric_dict(text)

    code = scorer.metrics["code_edit"].calculate(
        predicted, reference, groundtruth_feature_scope=meta
    )
    result["code_edit"] = metric_dict(code)

    formula_metric = scorer.metrics["formula_edit"]
    formula_available = bool(formula_metric._extract_formula_content(reference, None).strip())
    table_metric = scorer.metrics["table_edit"]
    table_available = bool(table_metric._extract_table_content(reference, None).strip())
    for name, available, content_type in (
        ("formula_edit", formula_available, "formula"),
        ("table_edit", table_available, "table"),
        ("table_TEDS", table_available, "table"),
    ):
        if not available:
            result[name] = unavailable(name, "Groundtruth contains no " + content_type)
        elif not ok:
            result[name] = zero(name, content_type)
        elif old.get(name, {}).get("success"):
            result[name] = copy.deepcopy(old[name])
        else:
            raise ValueError(f"Prior scorer omitted reference-scoped {name}")

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


def apply(source: Path, corrected_dataset: Path, manifest_path: Path,
          scorer_repo: Path, scorer_commit: str, output: Path) -> dict:
    if output.exists():
        raise ValueError("Scoring correction output already exists")
    validate_teds_revision(source)
    source_dataset = json.loads((source / "dataset.json").read_text(encoding="utf-8"))
    source_cases = {case["id"]: case for case in source_dataset["cases"]}
    corrected = {row["track_id"]: row for row in read_rows(corrected_dataset)}
    if len(source_cases) != 545 or set(source_cases) != set(corrected):
        raise ValueError("Corrected dataset population differs")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    corrections = {row["track_id"]: row for row in manifest.get("corrections", [])}
    if manifest.get("schema_version") != 1 or len(corrections) != len(manifest.get("corrections", [])):
        raise ValueError("Invalid dataset correction manifest")
    changed_references, changed_metadata = set(), set()
    for case_id, before in source_cases.items():
        after = corrected[case_id]
        if after["url"] != before["url"] or text_digest(after["html"]) != before["input_html_sha256"]:
            raise ValueError("Frozen input changed: " + case_id)
        reference_changed = text_digest(after["groundtruth_content"]) != before["reference_sha256"]
        metadata_changed = after.get("meta") != before.get("meta")
        if reference_changed:
            changed_references.add(case_id)
        if metadata_changed:
            changed_metadata.add(case_id)
        if reference_changed or metadata_changed:
            item = corrections.get(case_id)
            if not item or item["url"] != before["url"] or item.get("html_sha256") != before["input_html_sha256"]:
                raise ValueError("Correction binding mismatch: " + case_id)
            if reference_changed and (
                item.get("original_groundtruth_sha256") != before["reference_sha256"]
                or item.get("replacement_sha256") != text_digest(after["groundtruth_content"])
            ):
                raise ValueError("Reference correction mismatch: " + case_id)
            if metadata_changed and (
                item.get("original_meta_sha256") != json_digest(before.get("meta"))
                or item.get("replacement_meta") != after.get("meta")
            ):
                raise ValueError("Metadata correction mismatch: " + case_id)
    newly_changed = changed_references | changed_metadata
    for case_id in set(corrections) - newly_changed:
        item = corrections[case_id]
        before = source_cases[case_id]
        already_applied = (
            item.get("replacement_sha256") == before.get("reference_sha256")
            or item.get("replacement_meta") == before.get("meta")
        )
        if not already_applied:
            raise ValueError("Correction manifest scope differs from changed dataset fields")
    if not newly_changed <= set(corrections):
        raise ValueError("Changed dataset field lacks a declared correction")

    records = read_rows(source / "results.jsonl")
    providers = source_dataset["coverage_plan"]["static_html"]
    expected = {(provider, case_id) for provider in providers for case_id in source_cases}
    if len(records) != len(expected) or {(r["provider"], r["case_id"]) for r in records} != expected:
        raise ValueError("Scoring correction requires the complete method-case matrix")
    with tempfile.TemporaryDirectory(prefix="webmainbench-scoring-cache-") as cache:
        scorer = calculator(scorer_repo, scorer_commit, Path(cache))
        validate_controls(scorer)
        revised = []
        for record in records:
            row = copy.deepcopy(record)
            artifact = contained_path(source, row["artifact_path"])
            if digest(artifact) != row["output_sha256"]:
                raise ValueError("Provider artifact changed: " + row["attempt_id"])
            case = corrected[row["case_id"]]
            predicted = artifact.read_text(encoding="utf-8") if row.get("ok") else ""
            metrics = revised_metrics(row["metrics"], scorer, predicted,
                                      case["groundtruth_content"], case["meta"], bool(row.get("ok")),
                                      row["case_id"] in changed_references)
            row["pre_scoring_correction_metrics"] = copy.deepcopy(row["metrics"])
            row["metrics"] = metrics
            row["meta"] = copy.deepcopy(case["meta"])
            row["reference_sha256"] = text_digest(case["groundtruth_content"])
            revised.append(row)

    summaries = json.loads((source / "score-summaries.json").read_text(encoding="utf-8"))
    for provider in providers:
        provider_rows = [row for row in revised if row["provider"] == provider]
        summary = summaries[provider]
        summary["pre_scoring_correction_aggregate"] = copy.deepcopy(summary["official_aggregate"])
        summary["official_aggregate"] = aggregate(provider_rows)
        summary["dimensions"] = {
            axis: {
                value: aggregate([row for row in provider_rows
                                  if category(corrected[row["case_id"]], axis) == value])
                for value in sorted({category(corrected[row["case_id"]], axis)
                                     for row in provider_rows})
            }
            for axis in ("language", "level", "table", "code", "equation")
        }
        summary["scorer"]["repository"] = "https://github.com/lexmount/WebMainBench"
        summary["scorer"]["commit"] = scorer_commit
        summary["scorer"]["population"] = "shared reference-defined populations"

    shutil.copytree(source, output)
    shutil.copy2(source / "results.jsonl", output / "pre-scoring-correction-results.jsonl")
    shutil.copy2(source / "dataset.json", output / "pre-scoring-correction-dataset.json")
    results_path = output / "results.jsonl"
    results_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in revised),
                            encoding="utf-8")
    for case_id, case in source_cases.items():
        corrected_case = corrected[case_id]
        case["meta"] = copy.deepcopy(corrected_case["meta"])
        case["reference_sha256"] = text_digest(corrected_case["groundtruth_content"])
        case["reference_chars"] = len(corrected_case["groundtruth_content"])
        if case_id in changed_references:
            contained_path(output / "references", f"{case_id}.md").write_text(
                corrected_case["groundtruth_content"], encoding="utf-8")
    source_dataset["source_dataset_sha256"] = digest(corrected_dataset)
    dataset_path = output / "dataset.json"
    dataset_path.write_text(json.dumps(source_dataset, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    (output / "score-summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    correction_source = output / "scoring-correction-source"
    correction_source.mkdir()
    shutil.copy2(manifest_path, correction_source / manifest_path.name)
    shutil.copy2(REGISTRY, correction_source / REGISTRY.name)
    for item in corrections.values():
        if item.get("replacement_file"):
            shutil.copy2(manifest_path.parent / item["replacement_file"],
                         correction_source / item["replacement_file"])
    receipt = {
        "schema_version": 1,
        "kind": "webmainbench_shared_population_scoring_correction",
        "provider_calls": 0,
        "logical_results": len(revised),
        "providers": providers,
        "changed_reference_cases": sorted(changed_references),
        "changed_metadata_cases": sorted(changed_metadata),
        "regression_ids": list(CONTROL_IDS),
        "regression_registry_sha256": digest(REGISTRY),
        "root_causes": [
            "Code extraction omitted inline and raw-HTML representations.",
            "Specialized metric denominators depended on each method's output.",
            "Extraction failures disappeared from metric aggregates.",
        ],
        "scorer_repository": "https://github.com/lexmount/WebMainBench",
        "scorer_commit": scorer_commit,
        "manifest_sha256": digest(manifest_path),
        "corrected_dataset_sha256": digest(corrected_dataset),
        "input_results_sha256": digest(source / "results.jsonl"),
        "output_results_sha256": digest(results_path),
        "input_dataset_sha256": digest(source / "dataset.json"),
        "output_dataset_sha256": digest(dataset_path),
    }
    (output / "scoring-correction.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    print(json.dumps(apply(args.source, args.corrected_dataset, args.manifest,
                           args.scorer_repo, args.scorer_commit, args.output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
