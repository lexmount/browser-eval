"""Validate score revisions and apply evidence-preserving TEDS corrections."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import shutil
import tempfile
import types
from collections import defaultdict
from pathlib import Path

from harness.record_io import load_jsonl
from lexbench_webfetch.metrics import task_passed
from harness.web_fetch.result_review import write_review_document
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.scoring_regressions import build_correction_provenance, load_scoring_regressions
from harness.web_fetch.webmainbench_evidence import require_matrix
from harness.web_fetch.webmainbench_scoring import (
    SCORER_COMMIT,
    aggregate,
    metric_dict,
    official_calculator,
)

from lexbench_webfetch.paths import contained_path
from harness.web_fetch.safety import evidence_tree_identity, reject_evidence_symlinks, stage_evidence_revision
from lexbench_webfetch.scoring_regressions import valid_correction_provenance
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
    _require_teds_matrix(json.loads(teds_dataset_path.read_text()), receipt, original, current)
    _require_finite_metrics(current)
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
    parser.add_argument("--scorer-commit", default=SCORER_COMMIT)
    args = parser.parse_args()
    receipt = revise_teds(
        args.source,
        args.output,
        args.scorer_repo,
        prior_corrected=args.prior_corrected,
        scorer_commit=args.scorer_commit,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "changed_scores": len(receipt["changed_scores"]),
                "provider_calls": 0,
            }
        )
    )


def revise_teds(source, output, scorer_repo, *, prior_corrected=None, scorer_commit=SCORER_COMMIT):
    """Stage a TEDS-only revision while retaining provider bytes and first official scores."""
    prior_identity = (
        evidence_tree_identity(prior_corrected) if prior_corrected is not None else None
    )

    def revise(staging):
        receipt = _revise_teds(source, staging, scorer_repo, prior_corrected, scorer_commit)
        if (
            prior_corrected is not None
            and evidence_tree_identity(prior_corrected) != prior_identity
        ):
            raise ValueError("Inherited correction evidence changed during revision")
        return receipt

    return stage_evidence_revision(source, output, revise)


def _revise_teds(source, output, scorer_repo, prior_corrected, scorer_commit):
    validate_controls()
    provenance = build_correction_provenance("structured-extraction", ROOT_CAUSE, CONTROLS)
    dataset = json.loads((source / "dataset.json").read_text())
    summaries = json.loads((source / "score-summaries.json").read_text())
    records = load_jsonl(source / "results.jsonl")
    inherited = {}
    inherited_receipt_sha256 = None
    if prior_corrected:
        validate_teds_revision(prior_corrected)
        inherited = {
            row["attempt_id"]: row for row in iter(load_jsonl(prior_corrected / "results.jsonl"))
        }
        inherited_receipt_sha256 = digest(prior_corrected / "teds-correction.json")
    providers = dataset["coverage_plan"]["static_html"]
    _require_teds_matrix(
        dataset, {"scope": {"providers": providers, "logical_results": len(records)}}, records
    )
    if set(summaries) != set(providers):
        raise ValueError("TEDS correction summaries differ from declared methods")
    _require_finite_metrics(records)
    for summary in summaries.values():
        for relative, expected in summary["scorer"]["metric_source_sha256"].items():
            path = contained_path(scorer_repo, relative, reject_symlinks=True)
            if not path.is_file():
                path = contained_path(
                    scorer_repo / "webmainbench/metrics", relative, reject_symlinks=True
                )
            if digest(path) != expected:
                raise ValueError("Official source differs: " + relative)
    with tempfile.TemporaryDirectory(prefix="webmainbench-teds-cache-") as cache_dir:
        calculator = official_calculator(scorer_repo, Path(cache_dir), scorer_commit)
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
        _require_finite_metrics(corrected)
        for provider, summary in summaries.items():
            rows = [row for row in corrected if row["provider"] == provider]
            summary.setdefault(
                "original_official_aggregate", copy.deepcopy(summary["official_aggregate"])
            )
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
        shutil.copytree(source, output)
        shutil.copy2(source / "results.jsonl", output / "original-results.jsonl")
        results_path = output / "results.jsonl"
        results_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in corrected)
        )
        (output / "score-summaries.json").write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2) + "\n"
        )
        from lexbench_webfetch import ordered_tree as ordered_tree_module, scoring as scoring_module

        def source_name(path):
            return path.relative_to(ROOT) if path.is_relative_to(ROOT) else Path("lexbench_webfetch") / path.name

        source_files = [
            Path(__file__).resolve(),
            Path(ordered_tree_module.__file__),
            Path(scoring_module.__file__),
            ROOT / "harness/web_fetch/safety.py",
            ROOT / "harness/web_fetch/webmainbench_scoring.py",
            ROOT / "harness/web_fetch/webmainbench_evidence.py",
        ]
        for path in source_files:
            target = output / "correction-source" / source_name(path)
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
            "scorer_commit": scorer_commit,
            "correction_provenance": provenance,
            "input_results_sha256": digest(source / "results.jsonl"),
            "output_results_sha256": digest(results_path),
            "dataset_sha256": digest(source / "dataset.json"),
            "sample_manifest_sha256": dataset.get("sample_manifest_sha256"),
            "inherited_correction_receipt_sha256": inherited_receipt_sha256,
            "inherited_logical_results": len(inherited),
            "source_code_sha256": {
                str(source_name(path)): digest(path) for path in source_files
            },
            "scope": {"logical_results": len(records), "providers": sorted(summaries)},
            "changed_scores": changed,
            "unavailable_exact_teds": unavailable,
            "denominator_changed": bool(unavailable),
            "latency_changed": False,
        }
        (output / "teds-correction.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        )
    return receipt


def _require_teds_matrix(dataset, receipt, *matrices):
    providers = receipt.get("scope", {}).get("providers", [])
    case_ids = [case["id"] for case in dataset["cases"]]
    if (
        not providers
        or len(providers) != len(set(providers))
        or not case_ids
        or len(case_ids) != len(set(case_ids))
    ):
        raise ValueError("TEDS correction requires unique declared methods and tasks")
    if receipt["scope"].get("logical_results") != len(providers) * len(case_ids):
        raise ValueError("TEDS correction declared result count differs from its matrix")
    for rows in matrices:
        require_matrix(rows, providers, set(case_ids))


def _require_finite_metrics(rows):
    for row in rows:
        for metric in row.get("metrics", {}).values():
            score = metric.get("score", 0)
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise ValueError("TEDS correction contains a non-finite metric score")


def review_teds_correction(source, corrected):
    """Verify a TEDS-only revision's matrix, arithmetic and preserved evidence offline."""
    reject_evidence_symlinks(source)
    reject_evidence_symlinks(corrected)
    receipt = json.loads((corrected / "teds-correction.json").read_text())
    if not valid_correction_provenance(receipt) or receipt.get("provider_calls") != 0:
        raise ValueError("Correction lacks executable regression provenance")
    for field, path in [
        ("input_results_sha256", source / "results.jsonl"),
        ("output_results_sha256", corrected / "results.jsonl"),
        ("dataset_sha256", corrected / "dataset.json"),
    ]:
        if digest(path) != receipt[field]:
            raise ValueError("Correction hash mismatch: " + field)
    for relative, expected in receipt["source_code_sha256"].items():
        if digest(contained_path(corrected / "correction-source", relative)) != expected:
            raise ValueError("Correction implementation snapshot changed: " + relative)
    if digest(corrected / "original-results.jsonl") != receipt["input_results_sha256"]:
        raise ValueError("Original official records were not preserved byte-for-byte")
    before = load_jsonl(source / "results.jsonl")
    after = load_jsonl(corrected / "results.jsonl")
    expected_count = len(json.loads((source / "dataset.json").read_text())["cases"]) * len(
        receipt["scope"]["providers"]
    )
    if len(before) != expected_count or len(after) != len(before):
        raise ValueError("Correction must cover the complete declared matrix")
    if len({row["attempt_id"] for row in after}) != len(after):
        raise ValueError("Duplicate corrected attempt")
    for name in (
        "dataset.json",
        "sample-manifest.json",
        "physical-attempts.jsonl",
        "execution.json",
        "result-review.json",
    ):
        source_exists = (source / name).exists()
        corrected_exists = (corrected / name).exists()
        if not source_exists and not corrected_exists:
            continue
        if source_exists != corrected_exists:
            raise ValueError("Correction changed evidence file presence: " + name)
        if digest(source / name) != digest(corrected / name):
            raise ValueError("Unexpected change: " + name)
    _require_teds_matrix(json.loads((source / "dataset.json").read_text()), receipt, before, after)
    _require_finite_metrics(after)
    changed = []
    for old, new in zip(before, after):
        if {k: v for k, v in new.items() if k not in ("metrics", "official_metrics")} != {
            k: v for k, v in old.items() if k not in ("metrics", "official_metrics")
        }:
            raise ValueError("Changed provider evidence: " + old["attempt_id"])
        if new["official_metrics"] != old.get("official_metrics", old["metrics"]):
            raise ValueError("Original official metrics were not preserved")
        for metric in ("text_edit", "code_edit", "formula_edit", "table_edit"):
            if new["metrics"].get(metric) != old["metrics"].get(metric):
                raise ValueError("Changed unrelated component: " + metric)
        if digest(contained_path(corrected, new["artifact_path"])) != new["output_sha256"]:
            raise ValueError("Changed raw output bytes")
        if not old["ok"]:
            if new["metrics"]:
                raise ValueError("Added scores to a failed conversion")
            continue
        a, b = old["metrics"]["table_TEDS"], new["metrics"]["table_TEDS"]
        if a["success"] != b["success"] and not (
            a["success"]
            and not b["success"]
            and "Exact tree calculation exceeds safe workspace bound" in b.get("error", "")
        ):
            raise ValueError("Unexpected denominator change")
        if a["score"] != b["score"]:
            changed.append(
                {"attempt_id": new["attempt_id"], "before": a["score"], "after": b["score"]}
            )
        values = [
            v["score"] for k, v in new["metrics"].items() if k != "overall" and v.get("success")
        ]
        if (
            abs(new["metrics"]["overall"]["score"] - (sum(values) / len(values) if values else 0))
            > 1e-12
        ):
            raise ValueError("Per-page overall mismatch")
    if changed != receipt["changed_scores"]:
        raise ValueError("Changed-score inventory mismatch")
    require_valid_teds_for_release(after)
    summaries = json.loads((corrected / "score-summaries.json").read_text())
    if set(summaries) != set(receipt["scope"]["providers"]):
        raise ValueError("TEDS correction summary methods differ from declared matrix")
    impacts = {}
    for provider, summary in summaries.items():
        records = [row for row in after if row["provider"] == provider]
        if len(records) != len(json.loads((source / "dataset.json").read_text())["cases"]):
            raise ValueError("Provider scope mismatch")
        current = summary["official_aggregate"]
        expected = aggregate(records)
        for name, score in expected["scores"].items():
            actual = current["scores"].get(name)
            if (
                isinstance(actual, bool)
                or not isinstance(actual, (int, float))
                or not math.isfinite(actual)
                or abs(score - actual) > 1e-12
                or (
                    name in expected["denominators"]
                    and current["denominators"].get(name) != expected["denominators"][name]
                )
            ):
                raise ValueError("Aggregate mismatch: " + provider + "/" + name)
        impacts[provider] = {
            "official": summary["original_official_aggregate"],
            "corrected": current,
        }
    return {
        "schema_version": 1,
        "status": "passed",
        "scope": "TEDS correction integrity and arithmetic, not a new provider run",
        "logical_results": expected_count,
        "changed_scores": len(changed),
        "provider_calls": 0,
        "output_results_sha256": digest(corrected / "results.jsonl"),
        "correction_receipt_sha256": digest(corrected / "teds-correction.json"),
        "review_script_sha256": digest(Path(__file__)),
        "impacts": impacts,
    }


def teds_review_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--corrected", type=Path, required=True)
    args = parser.parse_args()
    output = args.corrected / "teds-correction-review.json"
    if output.exists() or output.is_symlink():
        raise ValueError("Refusing to overwrite the correction audit")
    result = review_teds_correction(args.source, args.corrected)

    def verify_unchanged():
        if review_teds_correction(args.source, args.corrected) != result:
            raise ValueError("TEDS correction evidence changed before audit installation")

    write_review_document(output, result, before_replace=verify_unchanged)
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "logical_results", "changed_scores", "provider_calls")
            }
        )
    )


def normalized_edit(predicted: str, reference: str) -> float | None:
    from rapidfuzz.distance import Levenshtein

    longest = max(len(predicted), len(reference))
    return None if longest == 0 else 1.0 - Levenshtein.distance(predicted, reference) / longest


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def independent_code(text: str) -> str:
    blocks = []
    for match in re.finditer(r"(```[\s\S]*?```)", text):
        content = "\n".join(match.group(0).split("\n")[1:-1])
        if content:
            blocks.append(content)
    pattern = r"(?:\n\s*\n)((?:(?: {4,}|\t+)[^\n]*(?:\n|$)){2,})(?=\n\s*\n|$)"
    for match in re.finditer(pattern, text, re.MULTILINE):
        raw = match.group(1);nonempty = [line.strip() for line in raw.split("\n") if line.strip()]
        if len(nonempty) < 2 or not all(line.startswith("    ") or line.startswith("\t") or not line.strip() for line in raw.split("\n") if line.strip()):
            continue
        if any(re.match(r"^[-•*]\s", line) or re.match(r"^\d+\.\s", line) or re.search(r"\$[\d,]", line) or re.search(r"\b(million|billion|thousand)\b", line, re.I) for line in nonempty):
            continue
        cleaned = [line[4:] if line.startswith("    ") else line[1:] if line.startswith("\t") else line for line in raw.split("\n") if line.strip()]
        if cleaned:
            blocks.append("\n".join(cleaned))
    return "\n".join(blocks)


def independent_formula(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", "", text)
    found = []
    for pattern in (r"(?<!\\)\$\$(.*?)(?<!\\)\$\$", r"(?<!\\)\\\[(.*?)(?<!\\)\\\]", r"(?<!\\)\$(.*?)(?<!\\)\$", r"(?<!\\)\\\((.*?)(?<!\\)\\\)"):
        found.extend(match.group(1).strip() for match in re.finditer(pattern, text, re.DOTALL) if match.group(1).strip())
    return "\n".join(found)


def close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-12


def sample_review_main(argv=None, *, root=None) -> None:
    from datetime import datetime, timezone

    from .webmainbench_evidence import load_sample_contract, read_jsonl, scored_shard_bindings
    from .webmainbench_scoring import CORE_METRICS, METRIC_SOURCE_MANIFEST_SHA256

    parser = argparse.ArgumentParser(description="Audit a profile-declared WebMainBench sample without the official scorer")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--single-shard", type=Path,
                        help="Use this immutable shard path when reviewing exactly one provider")
    parser.add_argument(
        "--providers",
        default=None,
        help="Comma-separated provider shards; the default is the publishable matrix",
    )
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("Review output exists; refusing to overwrite")
    source_records, sample, profile = load_sample_contract(args.dataset, args.sample_manifest, args.profile)
    expected_ids = {row["track_id"] for row in source_records}
    expected_count = len(expected_ids)
    sample_sha256 = digest(args.sample_manifest)
    providers = list(profile["providers"]) if args.providers is None else [value.strip() for value in args.providers.split(",") if value.strip()]
    if not providers or len(set(providers)) != len(providers) or not set(providers) <= set(profile["providers"]):
        raise ValueError("Providers must be a unique, non-empty subset of the declared provider matrix")
    if args.single_shard and len(providers) != 1:
        raise ValueError("--single-shard requires exactly one provider")
    shards = {provider: args.single_shard or args.source_root / provider for provider in providers}
    fingerprints = scored_shard_bindings(args.source_root, providers, shards=shards)
    authority_bindings = {path: digest(path) for path in (args.dataset, args.sample_manifest, args.profile)}
    source = {row["track_id"]: row for row in source_records}
    scorer_commit = profile["official_scorer_commit"]
    metric_manifest = profile.get("metric_source_manifest_sha256", METRIC_SOURCE_MANIFEST_SHA256)
    core = CORE_METRICS
    failures = []
    counts = {"logical": 0, "physical": 0, "artifacts": 0, "text_recomputed": 0,
              "code_recomputed": 0, "formula_recomputed": 0, "aggregate_recomputed": 0,
              "network_isolation_probes": 0, "scorer_bindings": 0}
    provider_notes = {}
    for provider in providers:
        root = args.single_shard if args.single_shard else args.source_root / provider
        logical = read_jsonl(root / "scored-results.jsonl")
        physical = read_jsonl(root / "physical-attempts.jsonl")
        summary = json.loads((root / "score-summary.json").read_text(encoding="utf-8"))
        execution = json.loads((root / "execution.json").read_text(encoding="utf-8"))
        if any(execution.get(key) != profile[key] for key in ("timeout_seconds", "max_failure_retries")):
            failures.append(f"{provider}: declared runtime contract")
        shard_dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
        selection = shard_dataset.get("selection_manifest", {})
        selection_sha = selection.get("sha256") or selection.get("sample_manifest_sha256")
        execution_selection = execution.get("selection_manifest") or execution.get("report_selection") or {}
        execution_selection_sha = execution_selection.get("sha256") or execution_selection.get("sample_manifest_sha256")
        if (selection_sha != sample_sha256 or execution_selection_sha != sample_sha256
                or set(shard_dataset.get("case_ids", [])) != expected_ids
                or shard_dataset.get("case_count") != expected_count):
            failures.append(f"{provider}: sample selection binding")
        scorer = summary.get("scorer", {})
        if (scorer.get("commit") != scorer_commit
                or scorer.get("metric_config") != {key: value for key, value in profile["metric_config"].items() if key != "reason"}
                or canonical_digest(scorer.get("metric_source_sha256", {}))
                    != metric_manifest):
            failures.append(f"{provider}: scorer source binding")
        else:
            counts["scorer_bindings"] += 1
        probe = execution.get("network_isolation_probe", {})
        if probe.get("mode") != "cloudflare_reject_external_resource_types":
            ca_artifact = contained_path(root, execution.get("fixture_ca_artifact", ""), reject_symlinks=True)
            if (probe.get("mode") != "macos_sandbox_exec_loopback_only"
                    or probe.get("external_connect_errno") not in (1, 13)
                    or not ca_artifact.is_file()
                    or digest(ca_artifact) != probe.get("fixture_ca_sha256")):
                failures.append(f"{provider}: network isolation probe")
            else:
                first = source[logical[0]["track_id"]]
                if probe.get("loopback_body_sha256") != hashlib.sha256(first["html"].encode()).hexdigest():
                    failures.append(f"{provider}: loopback probe body hash")
        counts["network_isolation_probes"] += 1
        counts["logical"] += len(logical);counts["physical"] += len(physical)
        if (len(logical) != expected_count
                or {row["track_id"] for row in logical} != expected_ids
                or {row.get("provider") for row in logical} != {provider}):
            failures.append(f"{provider}: incomplete logical matrix")
            continue
        by_id = {}
        for attempt in physical:
            by_id.setdefault(attempt["track_id"], []).append(attempt)
        if set(by_id) != expected_ids or {row.get("provider") for row in physical} != {provider}:
            failures.append(f"{provider}: physical attempt matrix")
        for row in logical:
            track_id = row["track_id"];record = source[track_id]
            artifact = contained_path(root, row["artifact_path"], reject_symlinks=True)
            if digest(artifact) != row["output_sha256"]:
                failures.append(f"{provider}/{track_id}: artifact hash")
                continue
            counts["artifacts"] += 1
            if hashlib.sha256(record["html"].encode()).hexdigest() != row["input_html_sha256"]:
                failures.append(f"{provider}/{track_id}: input hash")
            if hashlib.sha256(record["groundtruth_content"].encode()).hexdigest() != row["reference_sha256"]:
                failures.append(f"{provider}/{track_id}: reference hash")
            attempts = by_id.get(track_id, [])
            expected_attempts = list(range(1, len(attempts) + 1))
            if (not attempts or len(attempts) > profile['max_failure_retries'] + 1
                    or len(attempts) != row.get("physical_attempt_count")
                    or [item.get("physical_attempt") for item in attempts] != expected_attempts
                    or any(item.get("ok") for item in attempts[:-1])
                    or row.get("retry_selection") != "final_physical_attempt_is_authoritative"
                    or attempts[-1]["ok"] != row["ok"]
                    or attempts[-1].get("error_type") != row.get("error_type")
                    or attempts[-1].get("output_sha256") != row.get("output_sha256")
                    or any(item.get("input_html_sha256") != row["input_html_sha256"] for item in attempts)
                    or any(item.get("reference_sha256") != row["reference_sha256"] for item in attempts)):
                failures.append(f"{provider}/{track_id}: final retry selection")
            if not close(sum(item["elapsed_ms"] for item in attempts), row["total_elapsed_ms"]):
                failures.append(f"{provider}/{track_id}: retry latency total")
            if not row["ok"]:
                if row.get("metrics"):
                    failures.append(f"{provider}/{track_id}: failed request has quality metrics")
                continue
            predicted = artifact.read_text(encoding="utf-8")
            text_score = normalized_edit(predicted, record["groundtruth_content"])
            observed = row["metrics"]["text_edit"]
            if text_score is None or not observed["success"] or not close(text_score, observed["score"]):
                failures.append(f"{provider}/{track_id}: independent text_edit")
            counts["text_recomputed"] += 1
            for metric, extractor, key in (("code_edit", independent_code, "code_recomputed"), ("formula_edit", independent_formula, "formula_recomputed")):
                expected = normalized_edit(extractor(predicted), extractor(record["groundtruth_content"]))
                observed = row["metrics"][metric]
                if expected is None:
                    if observed["success"]:
                        failures.append(f"{provider}/{track_id}: {metric} should be unavailable")
                elif not observed["success"] or not close(expected, observed["score"]):
                    failures.append(f"{provider}/{track_id}: independent {metric}")
                counts[key] += 1
        totals = {name: 0.0 for name in core};denominators = {name: 0 for name in core}
        for row in logical:
            for name in core:
                metric = row.get("metrics", {}).get(name, {})
                if metric and metric.get("details", {}).get("availability") != "reference_not_applicable":
                    totals[name] += metric["score"] if metric.get("success") else 0.0
                    denominators[name] += 1
        scores = {name: totals[name] / denominators[name] if denominators[name] else 0.0 for name in core}
        scores["overall"] = sum(scores.values()) / len(core)
        official = summary["official_aggregate"]
        if denominators != official["denominators"] or any(not close(value, official["scores"][name]) for name, value in scores.items()):
            failures.append(f"{provider}: independent aggregate")
        counts["aggregate_recomputed"] += 1
        provider_notes[provider] = {
            "request_failures": sum(not row["ok"] for row in logical),
            "physical_attempts": len(physical),
            "metric_denominators": denominators,
        }
    expected_logical = expected_count * len(providers)
    checks = [
        {"id": "matrix", "status": "passed" if counts["logical"] == expected_logical else "failed", "detail": f"{counts['logical']}/{expected_logical} logical results"},
        {"id": "artifacts", "status": "passed" if counts["artifacts"] == counts["logical"] else "failed", "detail": f"{counts['artifacts']} artifact hashes matched"},
        {"id": "retry-accounting", "status": "passed" if not any("retry" in item for item in failures) else "failed", "detail": f"{counts['physical']} physical attempts checked against final-result selection and total latency"},
        {"id": "network-isolation", "status": "passed" if not any("isolation" in item or "loopback probe" in item or "resource rejection" in item for item in failures) else "failed", "detail": f"{counts['network_isolation_probes']}/{len(providers)} execution-bound isolation probes checked"},
        {"id": "official-scorer-binding", "status": "passed" if counts["scorer_bindings"] == len(providers) else "failed", "detail": f"{counts['scorer_bindings']}/{len(providers)} summaries bind commit {scorer_commit[:9]}, use_llm=false and the pinned metric-source manifest"},
        {"id": "independent-text-edit", "status": "passed" if not any("text_edit" in item for item in failures) else "failed", "detail": f"{counts['text_recomputed']} successful outputs recomputed without importing WebMainBench"},
        {"id": "independent-code-formula", "status": "passed" if not any("code_edit" in item or "formula_edit" in item for item in failures) else "failed", "detail": f"{counts['code_recomputed']} code and {counts['formula_recomputed']} formula metric cases recomputed"},
        {"id": "aggregate", "status": "passed" if not any("aggregate" in item for item in failures) else "failed", "detail": f"{counts['aggregate_recomputed']}/{len(providers)} provider aggregates and denominators recomputed"},
    ]
    result = {
        "schema_version": 1,
        "status": "passed" if not failures and all(check["status"] == "passed" for check in checks) else "failed",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewer": "independent deterministic audit; official scorer is not imported for text, code or formula recomputation; table metrics are source-bound and aggregate-checked",
        "providers": list(providers),
        "checks": checks,
        "provider_notes": provider_notes,
        "failures": failures[:200],
        "review_script_sha256": digest(Path(__file__)),
        "dataset_sha256": profile["source_dataset_sha256"],
        "profile_sha256": digest(args.profile),
        "bindings": {"provider_files_sha256": fingerprints},
        "sample_manifest_sha256": sample_sha256,
        "sample_case_count": expected_count,
    }
    def unchanged():
        if (scored_shard_bindings(args.source_root, providers, shards=shards) != fingerprints
                or any(contained_path(path.parent, path.name, reject_symlinks=True).is_symlink()
                       or digest(path) != expected for path, expected in authority_bindings.items())):
            raise ValueError("Sample review inputs changed during audit")
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Review output appeared during audit")

    write_review_document(args.output, result, before_replace=unchanged)
    print(json.dumps({"status": result["status"], "failures": len(failures), **counts}, ensure_ascii=False))
    if result["status"] != "passed":
        raise SystemExit(1)


QUERY_NAVIGATION = re.compile(r"(?:window\.)?location\.search\s*(?:\+?=)")
ORIGIN_NAVIGATION = re.compile(
    r"(?:window\.)?location\.href\s*=\s*(?:[A-Za-z_$][\w$]*|['\"](?:https?:)?//)"
)
SCORE_CLIFF_LOW = 0.05
SCORE_CLIFF_PEER = 0.5


def read_suspicion_jsonl(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_suspicions(evidence: Path, source_dataset: Path) -> dict:
    reject_evidence_symlinks(evidence)
    if source_dataset.is_symlink():
        raise ValueError("Source dataset must not be symlinked")
    bindings = {
        path: digest(path)
        for path in (
            source_dataset,
            evidence / "dataset.json",
            evidence / "results.jsonl",
            evidence / "physical-attempts.jsonl",
        )
    }
    dataset = json.loads((evidence / "dataset.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in dataset["cases"]}
    if not cases or len(cases) != len(dataset["cases"]):
        raise ValueError("Frozen dataset must contain unique pages")
    source_rows = [
        case for case in read_suspicion_jsonl(source_dataset) if case["track_id"] in cases
    ]
    source = {case["track_id"]: case for case in source_rows}
    if len(source) != len(source_rows):
        raise ValueError("Source dataset contains duplicate pages")
    if set(source) != set(cases):
        raise ValueError("Source dataset does not cover the frozen report sample")
    for case_id, case in cases.items():
        original = source[case_id]
        if (
            text_digest(original["html"]) != case["input_html_sha256"]
            or text_digest(original["groundtruth_content"]) != case["reference_sha256"]
        ):
            raise ValueError("Source/reference binding changed for " + case_id)
    records = read_suspicion_jsonl(evidence / "results.jsonl")
    keys = [(row["provider"], row["case_id"]) for row in records]
    if not records or len(keys) != len(set(keys)):
        raise ValueError("Logical results must contain one result per provider and page")
    physical = read_suspicion_jsonl(evidence / "physical-attempts.jsonl")
    by_case: dict[str, list[dict]] = defaultdict(list)
    signals: dict[str, set[str]] = defaultdict(set)
    short_outputs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        case_id = record["case_id"]
        if case_id not in cases:
            raise ValueError("Unexpected result case " + case_id)
        by_case[case_id].append(record)
        artifact = contained_path(evidence, record["artifact_path"])
        raw = artifact.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.get("output_sha256"):
            raise ValueError("Output hash binding is absent or changed")
        output = raw.decode("utf-8", errors="replace")
        bindings[artifact] = hashlib.sha256(raw).hexdigest()
        attempt_id = record["attempt_id"]
        if not record["ok"]:
            signals[attempt_id].add("conversion-undelivered")
            if record.get("provider_metadata", {}).get("upstream_status") is None:
                signals[attempt_id].add("failure-without-upstream-status")
        elif not output.strip():
            signals[attempt_id].add("delivered-empty-output")
        if ERROR_PAGE.search(output):
            signals[attempt_id].add("error-page-counted-as-output")
        if record["ok"] and len(output.strip()) <= 512 and output.strip():
            short_outputs[(record["provider"], re.sub(r"\s+", " ", output).strip())].append(record)
        for metric, item in record.get("metrics", {}).items():
            if metric == "overall" or item.get("success") or not item.get("error"):
                continue
            if (
                "Both predicted and groundtruth are empty" not in item["error"]
                and "Skipped due to table_edit failure" not in item["error"]
            ):
                signals[attempt_id].add("component-scorer-error")
        teds = record.get("metrics", {}).get("table_TEDS", {})
        details = teds.get("details", {})
        if all(key in details for key in ("predicted_nodes", "groundtruth_nodes", "edit_distance")):
            minimum_edits = abs(details["predicted_nodes"] - details["groundtruth_nodes"])
            if details["edit_distance"] + 1e-9 < minimum_edits:
                signals[attempt_id].add("teds-distance-below-node-count-lower-bound")
    for rows in short_outputs.values():
        if len({row["case_id"] for row in rows}) > 1:
            for row in rows:
                signals[row["attempt_id"]].add("identical-short-output-across-pages")
    for case_id, rows in by_case.items():
        html = source[case_id]["html"]
        has_404 = any(
            "error-page-counted-as-output" in signals.get(row["attempt_id"], ())
            and "404"
            in contained_path(evidence, row["artifact_path"]).read_text(
                encoding="utf-8", errors="replace"
            )
            for row in rows
        )
        if has_404 and QUERY_NAVIGATION.search(html):
            for row in rows:
                signals[row["attempt_id"]].add("fixture-query-navigation-candidate")
        if ORIGIN_NAVIGATION.search(html) and any(
            not row["ok"] or "error-page-counted-as-output" in signals.get(row["attempt_id"], ())
            for row in rows
        ):
            for row in rows:
                signals[row["attempt_id"]].add("frozen-page-origin-navigation-candidate")
        if len({row["ok"] for row in rows}) > 1:
            for row in rows:
                signals[row["attempt_id"]].add("same-page-delivery-divergence")
        scored = [(row, row.get("metrics", {}).get("text_edit", {}).get("score")) for row in rows]
        if any(score is not None and score >= SCORE_CLIFF_PEER for _, score in scored):
            for row, score in scored:
                if score is not None and score <= SCORE_CLIFF_LOW:
                    signals[row["attempt_id"]].add("text-score-cliff-versus-peer")
        if len(html) >= 1_000_000:
            for row in rows:
                if not row["ok"]:
                    signals[row["attempt_id"]].add("large-inline-html-delivery-failure")
    result_ids = {row["attempt_id"] for row in records}
    if len(result_ids) != len(records):
        raise ValueError("Duplicate logical attempt IDs")
    if not physical or not {row["track_id"] for row in physical}.issubset(cases):
        raise ValueError("Physical-call inventory is missing or outside sample")
    counts: dict[str, int] = defaultdict(int)
    for values in signals.values():
        for value in values:
            counts[value] += 1
    flagged = [
        {
            "attempt_id": row["attempt_id"],
            "case_id": row["case_id"],
            "provider": row["provider"],
            "machine_delivery": bool(row["ok"]),
            "signals": sorted(signals[row["attempt_id"]]),
        }
        for row in records
        if signals.get(row["attempt_id"])
    ]
    if any(path.is_symlink() or digest(path) != expected for path, expected in bindings.items()):
        raise ValueError("Evidence changed during anomaly scan")
    return {
        "schema_version": 1,
        "kind": "investigation_signals_not_verdicts",
        "sample_pages": len(cases),
        "logical_results": len(records),
        "physical_calls": len(physical),
        "flagged_results": len(flagged),
        "signal_counts": dict(sorted(counts.items())),
        "results": flagged,
    }


def suspicion_main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Flag frozen WebMainBench evidence anomalies without changing scores."
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    from .safety import unlinked_path, write_new_file

    if args.output:
        args.output = unlinked_path(args.output)
        if args.output.exists():
            raise ValueError("Refusing to overwrite an anomaly scan")
    result = scan_suspicions(args.evidence, args.source_dataset)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        write_new_file(args.output, payload.encode("utf-8"))
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "sample_pages",
                    "logical_results",
                    "physical_calls",
                    "flagged_results",
                    "signal_counts",
                )
            },
            ensure_ascii=False,
        )
    )
