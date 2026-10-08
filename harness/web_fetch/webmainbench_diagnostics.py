"""Validate score revisions and apply evidence-preserving TEDS corrections."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import tempfile
import types
from pathlib import Path

from harness.record_io import load_jsonl
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_regressions import (
    build_correction_provenance,
    load_scoring_regressions,
)
from harness.web_fetch.webmainbench_scoring import aggregate, metric_dict, official_calculator

from .safety import contained_path
from .scoring_regressions import valid_correction_provenance
from .webmainbench_latency_correction import prior_results_path

ERROR_PAGE = re.compile(
    "(?:^#\\s*Error response\\b|^Error code:\\s*404\\b|ERR[_\\\\]ACCESS[_\\\\]DENIED\\b|"
    "^#\\s*无法访问此网站\\b|^This site can.t be reached\\b)",
    re.I | re.M,
)


def require_valid_teds_for_release(records):
    """A reproduced official defect may be inspected locally, never released."""
    invalid = []
    for row in records:
        details = row.get("metrics", {}).get("table_TEDS", {}).get("details", {})
        if all(key in details for key in ("predicted_nodes", "groundtruth_nodes", "edit_distance")):
            lower_bound = abs(details["predicted_nodes"] - details["groundtruth_nodes"])
            if details["edit_distance"] + 1e-09 < lower_bound:
                invalid.append(row.get("attempt_id", row.get("case_id", "unknown")))
    if invalid:
        raise ValueError(
            f"Publication blocked: {len(invalid)} impossible TEDS distances; "
            "resolve the scorer defect and revalidate affected scores before release"
        )


def validate_teds_revision(root):
    """Verify the self-contained revision chain before using corrected scores."""
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("Refusing symlinked TEDS correction evidence")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    receipt_path = contained_path(root, "teds-correction.json")
    reference_receipt_path = root / "reference-correction.json"
    scoring_receipt_path = root / "scoring-correction.json"
    if reference_receipt_path.is_file():
        teds_output_path = contained_path(root, "pre-reference-correction-results.jsonl")
        teds_dataset_path = contained_path(root, "pre-reference-correction-dataset.json")
    elif scoring_receipt_path.is_file():
        teds_output_path = contained_path(root, "pre-scoring-correction-results.jsonl")
        teds_dataset_path = contained_path(root, "pre-scoring-correction-dataset.json")
    else:
        teds_output_path = prior_results_path(root)
        teds_dataset_path = contained_path(root, "dataset.json")
    receipt = json.loads(receipt_path.read_text())
    review = json.loads(contained_path(root, "teds-correction-review.json").read_text())
    if not valid_correction_provenance(receipt) or receipt.get("provider_calls") != 0:
        raise ValueError("Invalid TEDS correction provenance")
    expected_count = receipt.get("scope", {}).get("logical_results")
    if review.get("status") != "passed" or review.get("logical_results") != expected_count:
        raise ValueError("TEDS correction review incomplete")
    if (
        digest(receipt_path) != review.get("correction_receipt_sha256")
        or digest(teds_output_path) != receipt.get("output_results_sha256")
        or receipt["output_results_sha256"] != review.get("output_results_sha256")
        or (digest(root / "original-results.jsonl") != receipt.get("input_results_sha256"))
        or (digest(teds_dataset_path) != receipt.get("dataset_sha256"))
    ):
        raise ValueError("TEDS correction evidence hash mismatch")
    for path, expected in receipt["source_code_sha256"].items():
        if digest(contained_path(root / "correction-source", path)) != expected:
            raise ValueError("TEDS correction source hash mismatch")
    original = load_jsonl(root / "original-results.jsonl")
    current = load_jsonl(teds_output_path)
    if len(original) != expected_count or len(current) != expected_count:
        raise ValueError("TEDS correction scope mismatch")
    if len({row["attempt_id"] for row in current}) != expected_count:
        raise ValueError("Duplicate corrected result")
    for before, after in zip(original, current):
        if after.get("official_metrics") != before.get("official_metrics", before.get("metrics")):
            raise ValueError("Official metrics changed in correction")
        left = {k: v for k, v in before.items() if k not in ("metrics", "official_metrics")}
        right = {k: v for k, v in after.items() if k not in ("metrics", "official_metrics")}
        if left != right:
            raise ValueError("Provider evidence changed in scoring correction")
        for metric in ("text_edit", "code_edit", "formula_edit", "table_edit"):
            if before["metrics"].get(metric) != after["metrics"].get(metric):
                raise ValueError("Unrelated metric changed in TEDS correction")
        prior_teds = before["metrics"].get("table_TEDS", {})
        current_teds = after["metrics"].get("table_TEDS", {})
        if prior_teds.get("success") != current_teds.get("success") and (
            not (
                prior_teds.get("success")
                and (not current_teds.get("success"))
                and (
                    "Exact tree calculation exceeds safe workspace bound"
                    in current_teds.get("error", "")
                )
            )
        ):
            raise ValueError("Unexpected TEDS scoring population change")
    require_valid_teds_for_release(current)
    return (receipt, review)


def validate_reference_revision(root):
    """Verify a score-only reference correction chained after TEDS correction."""
    receipt_path = contained_path(root, "reference-correction.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    prior_results = contained_path(root, "pre-reference-correction-results.jsonl")
    prior_dataset = contained_path(root, "pre-reference-correction-dataset.json")
    current_results = contained_path(
        root,
        "pre-scoring-correction-results.jsonl"
        if (root / "scoring-correction.json").is_file()
        else "results.jsonl",
    )
    current_dataset = contained_path(
        root,
        "pre-scoring-correction-dataset.json"
        if (root / "scoring-correction.json").is_file()
        else "dataset.json",
    )
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "webmainbench_reference_correction_v1"
        or receipt.get("provider_calls") != 0
        or (
            receipt.get("input_results_sha256")
            != hashlib.sha256(prior_results.read_bytes()).hexdigest()
        )
        or (
            receipt.get("output_results_sha256")
            != hashlib.sha256(current_results.read_bytes()).hexdigest()
        )
        or (
            receipt.get("prior_evidence_dataset_sha256")
            != hashlib.sha256(prior_dataset.read_bytes()).hexdigest()
        )
        or (
            receipt.get("output_evidence_dataset_sha256")
            != hashlib.sha256(current_dataset.read_bytes()).hexdigest()
        )
    ):
        raise ValueError("Invalid WebMainBench reference correction binding")
    previous = load_jsonl(prior_results)
    current = load_jsonl(current_results)
    if len(previous) != receipt.get("logical_results") or len(current) != len(previous):
        raise ValueError("Reference correction result scope mismatch")
    corrections = {item["track_id"]: item for item in receipt.get("corrections", [])}
    affected = 0
    for before, after in zip(previous, current):
        if before["attempt_id"] != after["attempt_id"]:
            raise ValueError("Reference correction reordered logical results")
        if before["case_id"] in corrections:
            affected += 1
            correction = corrections[before["case_id"]]
            if (
                before["reference_sha256"] != correction["original_reference_sha256"]
                or after["reference_sha256"] != correction["replacement_reference_sha256"]
                or after.get("pre_reference_correction_metrics") != before.get("metrics")
            ):
                raise ValueError("Reference correction metrics are not traceable")
            left = {k: v for k, v in before.items() if k not in ("metrics", "reference_sha256")}
            right = {
                k: v
                for k, v in after.items()
                if k not in ("metrics", "reference_sha256", "pre_reference_correction_metrics")
            }
        else:
            left, right = (before, after)
        if left != right:
            raise ValueError("Reference correction changed provider evidence")
    if affected != receipt.get("affected_logical_results"):
        raise ValueError("Reference correction affected scope mismatch")
    require_valid_teds_for_release(current)
    return receipt


def validate_webmainbench_scoring_revision(root):
    """Verify the zero-call shared-population correction and provider identity."""
    receipt = json.loads(contained_path(root, "scoring-correction.json").read_text())
    before_results = contained_path(root, "pre-scoring-correction-results.jsonl")
    after_results = contained_path(root, "results.jsonl")
    before_dataset = contained_path(root, "pre-scoring-correction-dataset.json")
    after_dataset = contained_path(root, "dataset.json")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "webmainbench_shared_population_scoring_correction"
        or receipt.get("provider_calls") != 0
        or (
            receipt.get("input_results_sha256")
            != hashlib.sha256(before_results.read_bytes()).hexdigest()
        )
        or (
            receipt.get("output_results_sha256")
            != hashlib.sha256(after_results.read_bytes()).hexdigest()
        )
        or (
            receipt.get("input_dataset_sha256")
            != hashlib.sha256(before_dataset.read_bytes()).hexdigest()
        )
        or (
            receipt.get("output_dataset_sha256")
            != hashlib.sha256(after_dataset.read_bytes()).hexdigest()
        )
    ):
        raise ValueError("Invalid WebMainBench shared-population correction binding")
    before = load_jsonl(before_results)
    after = load_jsonl(after_results)
    if len(before) != len(after) or len(after) != receipt.get("logical_results"):
        raise ValueError("WebMainBench scoring correction result scope mismatch")
    allowed = {"metrics", "meta", "reference_sha256", "pre_scoring_correction_metrics"}
    for left, right in zip(before, after):
        if (
            left["attempt_id"] != right["attempt_id"]
            or right.get("pre_scoring_correction_metrics") != left.get("metrics")
            or {key: value for key, value in left.items() if key not in allowed}
            != {key: value for key, value in right.items() if key not in allowed}
        ):
            raise ValueError("WebMainBench scoring correction changed provider evidence")
    expected_denominators = {
        "text_edit": 545,
        "code_edit": 110,
        "formula_edit": 257,
        "table_edit": 179,
        "table_TEDS": 179,
    }
    providers = receipt.get("providers", [])
    actual_providers = {row["provider"] for row in after}
    if not providers or len(providers) != len(set(providers)) or set(providers) != actual_providers:
        raise ValueError("WebMainBench scoring correction provider scope mismatch")
    for provider in providers:
        rows = [row for row in after if row["provider"] == provider]
        if len(rows) != expected_denominators["text_edit"]:
            raise ValueError("WebMainBench scoring correction provider matrix is incomplete")
        counts = {
            name: sum(row["metrics"][name].get("success", False) for row in rows)
            for name in expected_denominators
        }
        if counts != expected_denominators:
            raise ValueError("WebMainBench methods do not share reference-defined denominators")
    require_valid_teds_for_release(after)
    return receipt


def validate_teds_revision_if_present(root, records):
    """A revision marker or receipt requires the complete verified chain."""
    if (root / "teds-correction.json").exists() or any(
        "official_metrics" in row for row in records
    ):
        result = validate_teds_revision(root)
        if (root / "reference-correction.json").is_file():
            validate_reference_revision(root)
        if (root / "scoring-correction.json").is_file():
            validate_webmainbench_scoring_revision(root)
        return result
    return None


ROOT = Path(__file__).resolve().parents[2]
CONTROLS = [
    "ordered-tree-identical-nested-content-is-perfect",
    "ordered-tree-missing-children-cannot-be-perfect",
]
ROOT_CAUSE = (
    "The pinned official TEDS implementation passes a serialized string to APTED, "
    "whose default child accessor sees no tree children. The correction passes explicit "
    "nodes with a children accessor and preserves official parsing and label costs; "
    "algorithm errors no longer fall back to an optimistic node-count difference."
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_official_metrics(row):
    """Keep the first official score snapshot across stacked score corrections."""
    return copy.deepcopy(row.get("official_metrics", row.get("metrics", {})))


def validate_controls():
    registry = load_scoring_regressions()
    for identifier in CONTROLS:
        row = registry[identifier]
        result = score_attempt(row["case"], row["artifact"].encode(), ok=row["ok"])
        if task_passed(row["ok"], result) != row["expected_task_pass"]:
            raise ValueError("Regression task predicate failed: " + identifier)
        if any((result[key] != value for key, value in row["expected_metrics"].items())):
            raise ValueError("Regression metric failed: " + identifier)


def corrected_distance(self, predicted, reference):
    result = score_attempt(
        {"url": "https://example.test/ordered-tree", "ordered_tree_reference": reference},
        json.dumps(predicted).encode(),
        ok=True,
    )
    return result["ordered_tree"]["edit_distance"]


def correct_teds():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scorer-repo", required=True, type=Path)
    parser.add_argument(
        "--prior-corrected",
        type=Path,
        help="Previously verified correction whose unchanged results are inherited",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite corrected evidence")
    validate_controls()
    provenance = build_correction_provenance("structured-extraction", ROOT_CAUSE, CONTROLS)
    source = args.source
    dataset = json.loads((source / "dataset.json").read_text())
    summaries = json.loads((source / "score-summaries.json").read_text())
    records = load_jsonl(source / "results.jsonl")
    inherited = {}
    inherited_receipt_sha256 = None
    if args.prior_corrected:
        validate_teds_revision(args.prior_corrected)
        inherited = {
            row["attempt_id"]: row
            for row in iter(load_jsonl(args.prior_corrected / "results.jsonl"))
        }
        inherited_receipt_sha256 = digest(args.prior_corrected / "teds-correction.json")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Refusing symlinked correction evidence")
    expected = {
        (provider, case["id"])
        for provider in dataset["coverage_plan"]["static_html"]
        for case in dataset["cases"]
    }
    if (
        len(records) != len(expected)
        or len(expected) != len(dataset["cases"]) * len(summaries)
        or {(row["provider"], row["case_id"]) for row in records} != expected
    ):
        raise ValueError(
            f"Correction requires the complete declared "
            f"{len(dataset['cases'])}-by-{len(summaries)} matrix"
        )
    for summary in summaries.values():
        for relative, expected in summary["scorer"]["metric_source_sha256"].items():
            path = args.scorer_repo / relative
            if not path.is_file():
                path = args.scorer_repo / "webmainbench/metrics" / relative
            if digest(path) != expected:
                raise ValueError("Official source differs: " + relative)
    with tempfile.TemporaryDirectory(prefix="webmainbench-teds-cache-") as cache_dir:
        calculator = official_calculator(args.scorer_repo, Path(cache_dir))
        metric = calculator.metrics["table_TEDS"]
        metric._tree_edit_distance = types.MethodType(corrected_distance, metric)
        from webmainbench.metrics.base import MetricResult

        corrected = []
        changed = []
        for index, original in enumerate(records, 1):
            row = copy.deepcopy(original)
            artifact = contained_path(source, row["artifact_path"])
            reference_path = contained_path(source, "references/" + row["case_id"] + ".md")
            if (
                digest(artifact) != row["output_sha256"]
                or digest(reference_path) != row["reference_sha256"]
            ):
                raise ValueError("Evidence binding mismatch: " + row["attempt_id"])
            prior = inherited.get(row["attempt_id"])
            if prior is not None:
                if prior.get("official_metrics") != original_official_metrics(row) or {
                    key: value
                    for key, value in prior.items()
                    if key not in ("metrics", "official_metrics")
                } != {
                    key: value
                    for key, value in row.items()
                    if key not in ("metrics", "official_metrics")
                }:
                    raise ValueError(
                        "Inherited correction differs from frozen provider evidence: "
                        + row["attempt_id"]
                    )
                row = copy.deepcopy(prior)
                old = original["metrics"].get("table_TEDS", {})
                current = row["metrics"].get("table_TEDS", {})
                if current.get("score") != old.get("score"):
                    changed.append(
                        {
                            "attempt_id": row["attempt_id"],
                            "before": old["score"],
                            "after": current["score"],
                        }
                    )
            else:
                row["official_metrics"] = original_official_metrics(row)
            if prior is None and row.get("ok"):
                prior = row["metrics"]["table_edit"]
                dependency = MetricResult(
                    "table_edit",
                    prior["score"],
                    prior.get("details", {}),
                    prior.get("success", False),
                    prior.get("error"),
                )
                current = metric_dict(
                    metric.calculate(
                        artifact.read_text(),
                        reference_path.read_text(),
                        table_edit_result=dependency,
                    )
                )
                old = row["metrics"]["table_TEDS"]
                if current["success"] != old["success"] and (
                    not (
                        old["success"]
                        and (not current["success"])
                        and (
                            "Exact tree calculation exceeds safe workspace bound"
                            in current.get("error", "")
                        )
                    )
                ):
                    raise ValueError("Unexpected denominator change: " + row["attempt_id"])
                row["metrics"]["table_TEDS"] = current
                successful = [
                    item["score"]
                    for key, item in row["metrics"].items()
                    if key != "overall" and item.get("success")
                ]
                if successful:
                    row["metrics"]["overall"]["score"] = sum(successful) / len(successful)
                    if current["success"]:
                        row["metrics"]["overall"]["details"]["individual_scores"]["table_TEDS"] = (
                            current["score"]
                        )
                    else:
                        row["metrics"]["overall"]["details"]["individual_scores"].pop(
                            "table_TEDS", None
                        )
                if current.get("score") != old.get("score"):
                    changed.append(
                        {
                            "attempt_id": row["attempt_id"],
                            "before": old["score"],
                            "after": current["score"],
                        }
                    )
            corrected.append(row)
            if index % 10 == 0:
                print(f"Scored {index}/{len(records)}; changed {len(changed)}", flush=True)
        require_valid_teds_for_release(corrected)
        for provider, summary in summaries.items():
            rows = [row for row in corrected if row["provider"] == provider]
            summary["original_official_aggregate"] = summary["official_aggregate"]
            summary["official_aggregate"] = aggregate(rows)
            cases = {case["id"]: case for case in dataset["cases"]}
            for axis, values in summary["dimensions"].items():
                for value in values:

                    def category(row):
                        item = cases[row["case_id"]]["meta"].get(axis)
                        if isinstance(item, list):
                            return "+".join(sorted(item)) if item else "none"
                        return str(item if item not in (None, "") else "unavailable")

                    values[value] = aggregate([row for row in rows if category(row) == value])
        shutil.copytree(source, args.output)
        shutil.copy2(source / "results.jsonl", args.output / "original-results.jsonl")
        results_path = args.output / "results.jsonl"
        results_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in corrected)
        )
        (args.output / "score-summaries.json").write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2) + "\n"
        )
        source_files = [
            Path(__file__),
            ROOT / "harness/web_fetch/ordered_tree.py",
            ROOT / "harness/web_fetch/scoring.py",
        ]
        for path in source_files:
            target = args.output / "correction-source" / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        unavailable = [
            row["attempt_id"]
            for row in corrected
            if row.get("official_metrics", {}).get("table_TEDS", {}).get("success")
            and (not row.get("metrics", {}).get("table_TEDS", {}).get("success"))
        ]
        receipt = {
            "schema_version": 2,
            "kind": "webmainbench_teds_tree_adapter_correction",
            "provider_calls": 0,
            "correction_provenance": provenance,
            "input_results_sha256": digest(source / "results.jsonl"),
            "output_results_sha256": digest(results_path),
            "dataset_sha256": digest(source / "dataset.json"),
            "sample_manifest_sha256": dataset.get("sample_manifest_sha256"),
            "inherited_correction_receipt_sha256": inherited_receipt_sha256,
            "inherited_logical_results": len(inherited),
            "source_code_sha256": {
                str(path.relative_to(ROOT)): digest(path) for path in source_files
            },
            "scope": {"logical_results": len(records), "providers": sorted(summaries)},
            "changed_scores": changed,
            "unavailable_exact_teds": unavailable,
            "denominator_changed": bool(unavailable),
            "latency_changed": False,
        }
        (args.output / "teds-correction.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
    print(
        json.dumps(
            {"output": str(args.output), "changed_scores": len(changed), "provider_calls": 0}
        )
    )
