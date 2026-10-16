"""Certify, validate and apply complete method-by-case corrections."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

from harness.record_io import load_jsonl
from harness.web_fetch.webmainbench_certification import verify_shard
from harness.web_fetch.webmainbench_diagnostics import corrected_distance
from harness.web_fetch.webmainbench_scoring import (
    aggregate,
    load_records,
    metric_dict,
    official_calculator,
    revised_metrics,
)

from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.web_fetch.safety import stage_evidence_revision
from harness.distribution import installed_data_path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path):
    return load_jsonl(path, skip_blank=True)


def validate(
    root: Path, original_rows: list[dict], expected_parent: str
) -> tuple[dict, list, list]:
    certificate = json.loads((root / "certificate.json").read_text())
    plan = certificate["preflight"]
    if (
        certificate.get("kind") != "webmainbench_origin_correction_v1"
        or plan != certificate.get("postflight")
        or plan["parent_results_sha256"] != expected_parent
        or (digest(root / "results.jsonl") != certificate["results_sha256"])
        or (digest(root / "physical-attempts.jsonl") != certificate["physical_sha256"])
    ):
        raise ValueError("Case correction certificate mismatch")
    rows = read_rows(root / "results.jsonl")
    physical = read_rows(root / "physical-attempts.jsonl")
    originals = {r["provider"]: r for r in original_rows if r["case_id"] == plan["case_id"]}
    selected = set(plan["providers"])
    if (
        not selected
        or not selected <= set(originals)
        or len(rows) != len(selected)
        or ({r["provider"] for r in rows} != selected)
    ):
        raise ValueError(
            "Case correction must cover every method declared by its plan exactly once"
        )
    for row in rows:
        old = originals[row["provider"]]
        if (
            row["case_id"] != plan["case_id"]
            or row["supersedes_attempt_id"] != old["attempt_id"]
            or any(row[key] != old[key] for key in ("input_html_sha256", "reference_sha256"))
            or any(
                row["provider_metadata"].get(key) != old["provider_metadata"].get(key)
                for key in (
                    "binary_sha256",
                    "browser_version",
                    "extractor_version",
                    "converter_sha256",
                )
            )
        ):
            raise ValueError("Case correction changes the frozen input or provider identity")
        if digest(contained_path(root, row["artifact_path"])) != row["output_sha256"]:
            raise ValueError("Corrected output changed")
        calls = [c for c in physical if c["provider"] == row["provider"]]
        if len(calls) != row["physical_attempt_count"] or not calls:
            raise ValueError("Correction physical calls incomplete")
    if len({c["attempt_id"] for c in physical}) != len(physical):
        raise ValueError("Duplicate correction call identity")
    for call in physical:
        if (
            call["track_id"] != plan["case_id"]
            or call["provider"] not in selected
            or digest(contained_path(root, call["physical_artifact_path"])) != call["output_sha256"]
        ):
            raise ValueError("Correction physical output changed")
    return (certificate, rows, physical)


def apply(
    root: Path,
    staging: Path,
    original_rows: list,
    summaries: dict,
    *,
    validation_rows: list | None = None,
    expected_parent: str | None = None,
) -> tuple[list, dict]:
    from harness.web_fetch.webmainbench_case_correction import dimensions
    from harness.web_fetch.webmainbench_scoring import aggregate

    certificate, replacements, calls = validate(
        root,
        validation_rows if validation_rows is not None else original_rows,
        expected_parent if expected_parent is not None else digest(staging / "results.jsonl"),
    )
    lookup = {r["provider"]: r for r in replacements}
    case_id = certificate["preflight"]["case_id"]
    result = [lookup.get(r["provider"], r) if r["case_id"] == case_id else r for r in original_rows]
    for row in replacements:
        path = contained_path(staging, row["artifact_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contained_path(root, row["artifact_path"]), path)
    for call in calls:
        path = contained_path(staging, call["physical_artifact_path"])
        shutil.copy2(contained_path(root, call["physical_artifact_path"]), path)
    previous = read_rows(staging / "physical-attempts.jsonl")
    history = [r for r in previous if r["track_id"] == case_id]
    current_calls = [r for r in previous if r["track_id"] != case_id] + calls
    for filename, values in [
        ("results.jsonl", result),
        ("physical-attempts.jsonl", current_calls),
        ("superseded-case-results.jsonl", [r for r in original_rows if r["case_id"] == case_id]),
        ("superseded-case-calls.jsonl", history),
    ]:
        (staging / filename).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in values)
        )
    revised = copy.deepcopy(summaries)
    for provider, summary in revised.items():
        selected = [r for r in result if r["provider"] == provider]
        summary["official_aggregate"] = aggregate(selected)
        summary["dimensions"] = dimensions(selected)
        summary["request_successes"] = sum(r["ok"] for r in selected)
        summary["request_success_rate"] = summary["request_successes"] / len(selected)
    (staging / "score-summaries.json").write_text(
        json.dumps(revised, ensure_ascii=False, indent=2) + "\n"
    )
    receipt = {
        **certificate,
        "current_results_sha256": digest(staging / "results.jsonl"),
        "superseded_execution_ms": sum(r["elapsed_ms"] for r in history),
        "superseded_physical_calls": len(history),
        "superseded_case_results_sha256": digest(staging / "superseded-case-results.jsonl"),
        "superseded_case_calls_sha256": digest(staging / "superseded-case-calls.jsonl"),
        "score_summaries_sha256": digest(staging / "score-summaries.json"),
    }
    (staging / "case-correction.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    return (result, revised)


ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def copy_contained_artifact(source_root, source_relative, output_root, output_relative):
    """Copy one evidence artifact without allowing either path to escape its root."""
    source = contained_path(source_root, source_relative, reject_symlinks=True)
    destination = contained_path(output_root, output_relative, reject_symlinks=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination
    return destination


def certify_case_correction(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "dataset", "scorer", "output", "fixed-binary"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--moli-binary", type=Path)
    parser.add_argument("--prior-correction", type=Path)
    parser.add_argument("--provider", help="Correct only this evidence provider alias")
    parser.add_argument("--runner-provider", help="Runner provider used for --provider")
    parser.add_argument("--case-id", required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("Correction destination already exists")
    originals = read_rows(args.source / "results.jsonl")
    selected = {r["provider"]: r for r in originals if r["case_id"] == args.case_id}
    source_dataset = json.loads((args.source / "dataset.json").read_text())
    declared = source_dataset["coverage_plan"]["static_html"]
    if set(selected) != set(declared):
        raise ValueError("Correction source method matrix is incomplete")
    providers = [args.provider] if args.provider else declared
    if args.provider and args.provider not in selected:
        raise ValueError("Correction provider is not in the source matrix")
    if args.provider and args.prior_correction:
        parser.error("--prior-correction cannot be combined with --provider")
    if not args.provider and len(providers) != 6:
        raise ValueError("Complete correction requires all six methods")
    source_sha256 = source_dataset["source_dataset_sha256"]
    source_case = load_records(args.dataset, source_sha256)[args.case_id]
    fixed_provider = args.provider or "moli-fixed"
    binaries = {fixed_provider: args.fixed_binary}
    if args.moli_binary:
        binaries["moli"] = args.moli_binary
    if not args.provider and (not args.prior_correction) and ("moli" not in binaries):
        parser.error("--moli-binary is required without --prior-correction")
    for name, binary in binaries.items():
        if digest(binary) != selected[name]["provider_metadata"]["binary_sha256"]:
            raise ValueError("Binary differs from frozen cohort: " + name)
    prior_rows = prior_physical = []
    prior_certificate_sha256 = None
    if args.prior_correction:
        certificate_path = args.prior_correction / "certificate.json"
        prior_certificate = json.loads(certificate_path.read_text())
        prior_plan = prior_certificate.get("preflight", {})
        if (
            prior_certificate.get("kind") != "webmainbench_origin_correction_v1"
            or prior_plan != prior_certificate.get("postflight")
            or prior_plan.get("case_id") != args.case_id
            or (prior_plan.get("source_sha256") != digest(args.dataset))
            or (set(prior_plan.get("providers", ())) != set(providers))
            or (
                prior_certificate.get("results_sha256")
                != digest(args.prior_correction / "results.jsonl")
            )
            or (
                prior_certificate.get("physical_sha256")
                != digest(args.prior_correction / "physical-attempts.jsonl")
            )
        ):
            raise ValueError("Prior origin correction certificate mismatch")
        prior_rows = read_rows(args.prior_correction / "results.jsonl")
        prior_physical = read_rows(args.prior_correction / "physical-attempts.jsonl")
        prior_certificate_sha256 = digest(certificate_path)
        inherited = {row["provider"]: row for row in prior_rows if row["provider"] != "moli-fixed"}
        if set(inherited) != set(providers) - {"moli-fixed"}:
            raise ValueError("Prior origin correction does not cover unchanged methods")
        for name, row in inherited.items():
            old = selected[name]
            if (
                row.get("supersedes_attempt_id") != old["attempt_id"]
                or any(
                    row.get(key) != old.get(key)
                    for key in ("input_html_sha256", "reference_sha256")
                )
                or any(
                    row["provider_metadata"].get(key) != old["provider_metadata"].get(key)
                    for key in (
                        "binary_sha256",
                        "browser_version",
                        "extractor_version",
                        "converter_sha256",
                    )
                )
                or (
                    digest(contained_path(args.prior_correction, row["artifact_path"]))
                    != row["output_sha256"]
                )
            ):
                raise ValueError("Prior origin correction differs from current method: " + name)
        if any(
            digest(contained_path(args.prior_correction, call["physical_artifact_path"]))
            != call["output_sha256"]
            for call in prior_physical
            if call["provider"] != "moli-fixed"
        ):
            raise ValueError("Prior origin correction physical artifact changed")
    plan = {
        "kind": "webmainbench_origin_correction_v1",
        "case_id": args.case_id,
        "providers": providers,
        "parent_results_sha256": digest(args.source / "results.jsonl"),
        "source_sha256": digest(args.dataset),
        "timeout_seconds": 45,
        "max_failure_retries": 1,
        "scorer_commit": subprocess.check_output(
            ["git", "-C", str(args.scorer), "rev-parse", "HEAD"], text=True
        ).strip(),
        "input_html_sha256": hashlib.sha256(source_case["html"].encode()).hexdigest(),
        "reference_sha256": hashlib.sha256(source_case["groundtruth_content"].encode()).hexdigest(),
        "reason": (
            "localhost substitution triggers the frozen page canonical-URL redirect; "
            "preserve the original URL while serving the same frozen bytes "
            "and blocking external resources"
        ),
        "selection": (
            "one explicitly declared provider alias; "
            "the final physical call was selected before execution"
        )
        if args.provider
        else (
            "five unchanged methods inherited from a bound prior certified correction; "
            "the final physical call for moli-fixed was selected before execution"
        )
        if args.prior_correction
        else "final physical call; all six methods selected before execution",
    }
    if prior_certificate_sha256:
        plan["prior_correction_certificate_sha256"] = prior_certificate_sha256
    args.output.mkdir(parents=True)
    write(args.output / "preflight.json", plan)
    selection = args.output / "case-ids.txt"
    selection.write_text(args.case_id + "\n")
    corrected = []
    physical = []
    for name in providers:
        if args.prior_correction and name != "moli-fixed":
            row = next(row for row in prior_rows if row["provider"] == name)
            corrected.append(row)
            for call in prior_physical:
                if call["provider"] != name:
                    continue
                target = call["physical_artifact_path"]
                copy_contained_artifact(args.prior_correction, target, args.output, target)
                physical.append(call)
            target = row["artifact_path"]
            copy_contained_artifact(args.prior_correction, target, args.output, target)
            print(name, row["ok"], row["output_chars"], "inherited", flush=True)
            continue
        provider = (
            args.runner_provider
            if name == args.provider and args.runner_provider
            else "moli"
            if name == "moli-fixed"
            else name
        )
        shard = args.output / name
        env = dict(os.environ)
        if name in binaries:
            env["MOLI_BINARY"] = str(binaries[name])
        version = selected[name]["provider_metadata"].get("browser_version", "").split()
        version_args = (
            ["--expected-version", version[-1]] if version and provider != "kitesurf" else []
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_webmainbench_benchmark.py"),
                "--provider",
                provider,
                "--dataset",
                str(args.dataset),
                "--output",
                str(shard),
                "--case-ids-file",
                str(selection),
                "--preserve-original-origin",
                "--timeout",
                "45",
                "--max-failure-retries",
                "1",
                "--expected-dataset-sha256",
                plan["source_sha256"],
                "--official-scorer-commit",
                plan["scorer_commit"],
                *version_args,
            ],
            env=env,
            cwd=ROOT,
            check=True,
        )
        verify_shard(
            shard,
            provider,
            {args.case_id},
            {"case_count": 1, "source_dataset_sha256": plan["source_sha256"]},
        )
        row = read_rows(shard / "results.jsonl")[0]
        old = selected[name]
        for key in ("binary_sha256", "browser_version", "extractor_version", "converter_sha256"):
            if old["provider_metadata"].get(key) != row["provider_metadata"].get(key):
                raise ValueError("Provider identity changed: " + name + "/" + key)
        for key in ("input_html_sha256", "reference_sha256"):
            if row[key] != plan[key]:
                raise ValueError("Frozen input changed")
        if not row["ok"] or not row["output_chars"]:
            raise ValueError("Correction still fails: " + name)
        calculator = official_calculator(args.scorer, shard / "score-cache", plan["scorer_commit"])
        output = (shard / row["artifact_path"]).read_text()
        kwargs = dict(
            predicted_content=output,
            groundtruth_content=source_case["groundtruth_content"],
            predicted_content_list=None,
            groundtruth_content_list=None,
        )
        row["official_metrics"] = {
            k: metric_dict(v) for k, v in calculator.calculate_all(**kwargs).items()
        }
        calculator.metrics["table_TEDS"]._tree_edit_distance = types.MethodType(
            corrected_distance, calculator.metrics["table_TEDS"]
        )
        row["metrics"] = {k: metric_dict(v) for k, v in calculator.calculate_all(**kwargs).items()}
        row["provider"] = name
        row["case_id"] = row["track_id"]
        row["attempt_id"] = name + "__" + args.case_id + "__" + row["run_id"]
        for call in read_rows(shard / "physical-attempts.jsonl"):
            call["provider"] = name
            call["attempt_id"] = name + "__" + call["run_id"] + "__" + call["attempt_id"]
            target = (
                "artifacts/"
                + name
                + "/"
                + call["run_id"]
                + "-"
                + Path(call["physical_artifact_path"]).name
            )
            copy_contained_artifact(shard, call["physical_artifact_path"], args.output, target)
            call["physical_artifact_path"] = target
            physical.append(call)
        target = "artifacts/" + name + "/" + row["run_id"] + ".md"
        copy_contained_artifact(shard, row["artifact_path"], args.output, target)
        row["artifact_path"] = target
        row["physical_artifact_path"] = physical[-1]["physical_artifact_path"]
        row["supersedes_attempt_id"] = old["attempt_id"]
        corrected.append(row)
        print(name, row["ok"], row["output_chars"], flush=True)
    if (
        digest(args.dataset) != plan["source_sha256"]
        or digest(args.source / "results.jsonl") != plan["parent_results_sha256"]
    ):
        raise ValueError("Evidence changed during correction")
    for name, binary in binaries.items():
        if digest(binary) != selected[name]["provider_metadata"]["binary_sha256"]:
            raise ValueError("Binary changed during correction")
    for filename, values in [("results.jsonl", corrected), ("physical-attempts.jsonl", physical)]:
        (args.output / filename).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in values)
        )
    write(
        args.output / "certificate.json",
        {
            "kind": plan["kind"],
            "preflight": plan,
            "postflight": plan,
            "results_sha256": digest(args.output / "results.jsonl"),
            "physical_sha256": digest(args.output / "physical-attempts.jsonl"),
        },
    )


def regression_registry_path() -> Path:
    """Keep optional revision data lookup out of ordinary library imports."""
    if (ROOT / "pyproject.toml").is_file():
        return ROOT / "evaluation/webmainbench-full/scoring-regressions.json"
    return installed_data_path("share/browser-harness-eval/webmainbench/scoring-regressions.json")


def text_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_corrected_cases(path):
    rows = read_rows(path)
    ids = [row["track_id"] for row in rows]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Corrected dataset must contain unique track IDs")
    for identifier in ids:
        safe_identifier(identifier)
    return dict(zip(ids, rows))


def revise_dataset_scoring(source, corrected_dataset, manifest_path, scorer_repo, scorer_commit, output):
    """Apply an authority-bound full-population revision into a new evidence directory."""
    return stage_evidence_revision(source, output, lambda staging: _revise_dataset_scoring(
        source, corrected_dataset, manifest_path, scorer_repo, scorer_commit, staging))


def rescore_case_correction(source, corrected_dataset, scorer_repo, scorer_commit, output):
    """Recompute a certified case correction without rewriting its provider calls."""
    return stage_evidence_revision(source, output, lambda staging: _rescore_case_correction(
        source, corrected_dataset, scorer_repo, scorer_commit, staging))


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


def json_digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text_digest(raw)


def validate_controls(scorer) -> None:
    document = json.loads(regression_registry_path().read_text(encoding="utf-8"))
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


def _revise_dataset_scoring(source: Path, corrected_dataset: Path, manifest_path: Path,
          scorer_repo: Path, scorer_commit: str, output: Path) -> dict:
    if output.exists():
        raise ValueError("Scoring correction output already exists")
    source_dataset = json.loads((source / "dataset.json").read_text(encoding="utf-8"))
    source_cases = {case["id"]: case for case in source_dataset["cases"]}
    corrected = load_corrected_cases(corrected_dataset)
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
        scorer = official_calculator(scorer_repo, Path(cache), scorer_commit)
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
        from .webmainbench_case_correction import dimensions
        summary["dimensions"] = dimensions(provider_rows)
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
    shutil.copy2(regression_registry_path(), correction_source / regression_registry_path().name)
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
        "regression_registry_sha256": digest(regression_registry_path()),
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


def _rescore_case_correction(source: Path, corrected_dataset: Path, scorer_repo: Path,
          scorer_commit: str, output: Path) -> dict:
    if output.exists():
        raise ValueError("Refusing to replace case-correction evidence: " + str(output))
    certificate = json.loads((source / "certificate.json").read_text(encoding="utf-8"))
    if (certificate.get("kind") != "webmainbench_origin_correction_v1"
            or certificate.get("preflight") != certificate.get("postflight")
            or certificate.get("results_sha256") != digest(source / "results.jsonl")
            or certificate.get("physical_sha256") != digest(source / "physical-attempts.jsonl")):
        raise ValueError("Invalid source case-correction certificate")
    cases = load_corrected_cases(corrected_dataset)
    case_id = certificate["preflight"]["case_id"]
    case = cases[case_id]
    plan = certificate["preflight"]
    if (text_digest(case["html"]) != plan["input_html_sha256"]
            or text_digest(case["groundtruth_content"]) != plan["reference_sha256"]):
        raise ValueError("Case rescoring must preserve its certified input and reference")
    rows = load_jsonl(source / "results.jsonl")
    if ({row["provider"] for row in rows} != set(certificate["preflight"]["providers"])
            or len(rows) != len(set(certificate["preflight"]["providers"]))
            or any(row["case_id"] != case_id for row in rows)):
        raise ValueError("Case-correction result scope mismatch")
    physical = read_rows(source / "physical-attempts.jsonl")
    if len({row["attempt_id"] for row in physical}) != len(physical):
        raise ValueError("Duplicate correction physical call")
    for call in physical:
        if (call["track_id"] != case_id or call["provider"] not in plan["providers"]
                or digest(contained_path(source, call["physical_artifact_path"], reject_symlinks=True))
                != call["output_sha256"]):
            raise ValueError("Correction physical evidence changed")
    if any(sum(call["provider"] == row["provider"] for call in physical)
           != row["physical_attempt_count"] for row in rows):
        raise ValueError("Correction physical calls are incomplete")
    with tempfile.TemporaryDirectory(prefix="webmainbench-case-rescore-") as cache:
        scorer = official_calculator(scorer_repo, Path(cache), scorer_commit)
        validate_controls(scorer)
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
        "regression_ids": list(CONTROL_IDS),
        "regression_registry_sha256": digest(regression_registry_path()),
        "root_cause": "Specialized metric populations and failed-call scoring use the shared corrected scorer.",
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


def scoring_revision_main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--corrected-dataset", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--scorer-repo", required=True, type=Path)
    parser.add_argument("--scorer-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(revise_dataset_scoring(args.source, args.corrected_dataset, args.manifest,
                           args.scorer_repo, args.scorer_commit, args.output),
                     ensure_ascii=False, indent=2))


def case_scoring_main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--corrected-dataset", required=True, type=Path)
    parser.add_argument("--scorer-repo", required=True, type=Path)
    parser.add_argument("--scorer-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(rescore_case_correction(args.source, args.corrected_dataset, args.scorer_repo,
                           args.scorer_commit, args.output), ensure_ascii=False, indent=2))
