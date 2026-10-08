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
from scripts.correct_webmainbench_references import (
    calculator,
    category,
    copy_contained_artifact,
    read_rows,
)
from scripts.score_webmainbench_results import CORE_METRICS, aggregate, metric_dict


REGISTRY = ROOT / "evaluation/webmainbench-full/scoring-regressions.json"
CONTROL_IDS = (
    "prediction-only-code-does-not-expand-population",
    "missing-reference-scoped-code-scores-zero",
    "raw-html-pre-is-scorable-code",
    "layout-indented-prose-is-not-code",
)


def copy_correction_sources(corrections: dict, manifest_path: Path,
                            correction_source: Path) -> None:
    """Copy declared replacement files without allowing either root to escape."""
    for item in corrections.values():
        if item.get("replacement_file"):
            copy_contained_artifact(
                manifest_path.parent,
                item["replacement_file"],
                correction_source,
                item["replacement_file"],
            )


def prepare_correction_source(destination: Path) -> None:
    """Replace inherited provenance when correcting an already corrected cohort."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()


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


def validate_population_binding(source: Path, source_dataset: dict,
                                corrected_rows: list[dict], manifest: dict) -> None:
    """Bind authority-declared corrections to the complete ordered 545-row dataset."""
    validate_correction_authority(manifest)
    if manifest.get("schema_version") != 3:
        raise ValueError("Invalid dataset correction manifest")
    cases = {case["id"]: case for case in source_dataset.get("cases", [])}
    original_rows = []
    for corrected in corrected_rows:
        case_id = corrected.get("track_id") or corrected.get("id")
        if case_id not in cases:
            raise ValueError("Corrected dataset contains an unknown case")
        row = copy.deepcopy(corrected)
        row["groundtruth_content"] = contained_path(
            source, f"references/{case_id}.md"
        ).read_text(encoding="utf-8")
        row["meta"] = copy.deepcopy(cases[case_id].get("meta"))
        original_rows.append(row)
    for label, rows, binding in (
        ("input", original_rows, manifest.get("input_population", {})),
        ("output", corrected_rows, manifest.get("output_population", {})),
    ):
        ids = [row.get("track_id") or row.get("id") for row in rows]
        valid = (len(rows) == binding.get("row_count") and len(ids) == len(set(ids))
                 and json_digest(ids) == binding.get("ordered_track_ids_sha256")
                 and json_digest(rows) == binding.get("rows_sha256"))
        already_corrected = label == "input" and rows == corrected_rows
        if not valid and not already_corrected:
            raise ValueError(f"Dataset correction {label} population binding mismatch")


def validate_correction_authority(manifest: dict) -> None:
    """Reject reference changes whose declared authority depends on method outcomes."""
    if manifest.get("schema_version") != 3:
        raise ValueError("Dataset corrections require decision-authority schema v3")
    contracts = manifest.get("decision_contracts")
    if not isinstance(contracts, dict) or not contracts:
        raise ValueError("Dataset corrections require decision contracts")
    for contract in contracts.values():
        evidence = contract.get("required_source_evidence") if isinstance(contract, dict) else None
        if not isinstance(contract, dict) or contract.get("changed_field") not in {
            "groundtruth_content", "meta",
        }:
            raise ValueError("Decision contract has an unsupported changed field")
        if (not isinstance(evidence, list) or "task_contract" not in evidence
                or not any(isinstance(value, str) and (
                    value.startswith("frozen_") or value.startswith("original_")
                ) for value in evidence)):
            raise ValueError("Decision contract lacks method-independent source authority")
    forbidden = {"method_score", "method_result", "provider_score", "provider_result"}

    def contains_forbidden_key(value) -> bool:
        if isinstance(value, dict):
            return bool(forbidden & set(value)) or any(
                contains_forbidden_key(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_forbidden_key(item) for item in value)
        return False

    for correction in manifest.get("corrections", []):
        if correction.get("decision_contract") not in contracts:
            raise ValueError("Dataset correction lacks a declared decision contract")
        if not isinstance(correction.get("rationale"), str) or not correction["rationale"].strip():
            raise ValueError("Dataset correction lacks an evidence-based rationale")
        if contains_forbidden_key(correction):
            raise ValueError("Method outcomes cannot authorize dataset corrections")
        fields = set()
        if any(key in correction for key in (
            "original_groundtruth_sha256", "replacement_sha256", "replacement_file",
        )):
            fields.add("groundtruth_content")
        if any(key in correction for key in ("original_meta_sha256", "replacement_meta")):
            fields.add("meta")
        declared = contracts[correction["decision_contract"]]["changed_field"]
        if fields != {declared}:
            raise ValueError("Correction fields differ from its decision contract")


def apply(source: Path, corrected_dataset: Path, manifest_path: Path,
          scorer_repo: Path, scorer_commit: str, output: Path) -> dict:
    if output.exists():
        raise ValueError("Scoring correction output already exists")
    source_dataset = json.loads((source / "dataset.json").read_text(encoding="utf-8"))
    source_cases = {case["id"]: case for case in source_dataset["cases"]}
    corrected = {row["track_id"]: row for row in read_rows(corrected_dataset)}
    if len(source_cases) != 545 or set(source_cases) != set(corrected):
        raise ValueError("Corrected dataset population differs")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_population_binding(source, source_dataset, list(corrected.values()), manifest)
    corrections = {row["track_id"]: row for row in manifest.get("corrections", [])}
    if len(corrections) != len(manifest.get("corrections", [])):
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
            declared = manifest["decision_contracts"][item["decision_contract"]]["changed_field"]
            actual_fields = ({"groundtruth_content"} if reference_changed else set()) | (
                {"meta"} if metadata_changed else set()
            )
            if actual_fields != {declared}:
                raise ValueError("Changed dataset fields differ from decision contract: " + case_id)
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
    if not (output / "original-results.jsonl").exists():
        shutil.copy2(source / "results.jsonl", output / "original-results.jsonl")
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
    prepare_correction_source(correction_source)
    shutil.copy2(manifest_path, correction_source / manifest_path.name)
    shutil.copy2(REGISTRY, correction_source / REGISTRY.name)
    copy_correction_sources(corrections, manifest_path, correction_source)
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
            "TEDS passed a serialized label to APTED and lacked a bounded exact-workspace path.",
            "Code extraction omitted inline and raw-HTML representations.",
            "Specialized metric denominators depended on each method's output.",
            "Extraction failures disappeared from metric aggregates.",
            "The dataset correction manifest did not bind the complete ordered population.",
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
