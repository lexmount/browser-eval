"""Score a certified WCXB cohort with the maintained official metric semantics."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import tempfile
from collections import Counter
from numbers import Real
from pathlib import Path

from lexbench_webfetch.paths import contained_path, safe_identifier

from harness.record_io import parse_jsonl
from harness.reporting.publication import atomic_create, json_bytes
from harness.web_fetch.safety import reject_evidence_symlinks
from harness.web_fetch.wcxb_dataset import validate_manifest
from harness.web_fetch.wcxb_latency import load_full_paired_latency
from harness.web_fetch.webmainbench_runner import load_wcxb_source, selected_wcxb_input

# WCXB metrics, Murrough Foley (2026), CC-BY-4.0. Adapted from evaluate.py
# at c039d5ee9f5a3a984a0e167e63aacd04e76e78a9; this is provenance, not code
# loaded from a dataset or an external checkout.
# Source: https://github.com/Murrough-Foley/web-content-extraction-benchmark
# License: https://creativecommons.org/licenses/by/4.0/
UPSTREAM_EVALUATOR_SHA256 = 'ae4ad6299e190177fbb04a7c1190077fc6086b3fa0f31fb37a6538a5e979c559'


def word_f1(predicted: str, reference: str) -> tuple[float, float, float]:
    """Official lowercase Unicode-word multiset precision, recall and F1."""
    predicted_words = re.findall(r'\w+', (predicted or '').lower())
    reference_words = re.findall(r'\w+', (reference or '').lower())
    if not reference_words:
        return (1.0, 1.0, 1.0) if not predicted_words else (0.0, 0.0, 0.0)
    if not predicted_words:
        return 0.0, 0.0, 0.0
    overlap = sum((Counter(predicted_words) & Counter(reference_words)).values())
    precision, recall = overlap / len(predicted_words), overlap / len(reference_words)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def snippet_check(text: str, snippets: list[str]) -> float:
    """Official case-insensitive literal substring fraction; no word gate."""
    return sum(snippet.lower() in text.lower() for snippet in snippets) / len(snippets) if snippets else 1.0


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_error(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"/(?:Users|home|private/var)/[^\s:]+", "[local path]", value)


def classify(row: dict, predicted: str, precision: float, recall: float, reference: str) -> str:
    if not row["ok"]:
        return "request_error"
    if not predicted.strip():
        return "empty_output"
    if not reference.strip():
        return "empty_reference"
    lowered = predicted[:2000].lower()
    if (
        any(
            value in lowered
            for value in ("this site can't be reached", "404 not found", "bad gateway")
        )
        and recall < 0.05
    ):
        return "error_page"
    if precision < 0.25 and recall < 0.25:
        return "low_precision_and_recall"
    if precision < 0.25:
        return "low_precision"
    if recall < 0.25:
        return "low_recall"
    return "none"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


class EvidenceInputs:
    """Guard exactly the evidence bytes consumed by scoring."""

    def __init__(self):
        self.hashes = {}

    def read(self, path: Path) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or linked WCXB evidence: " + str(path))
        raw = path.read_bytes()
        value = hashlib.sha256(raw).hexdigest()
        if path in self.hashes and self.hashes[path] != value:
            raise ValueError("WCXB evidence changed during scoring")
        self.hashes[path] = value
        return raw

    def check(self):
        for path, value in list(self.hashes.items()):
            if path.is_symlink() or not path.is_file() or digest(path) != value:
                raise ValueError("WCXB evidence changed during scoring")


def participant_names(profile: dict) -> list[str]:
    providers = profile.get("providers")
    if (
        profile.get("comparison_kind") != "wcxb_frozen_html_to_markdown"
        or not isinstance(providers, list)
        or not providers
        or any(not isinstance(name, str) for name in providers)
        or len(set(providers)) != len(providers)
    ):
        raise ValueError("Invalid WCXB participant profile")
    for name in providers:
        safe_identifier(name)
    timeout, retries = profile.get("timeout_seconds"), profile.get("max_failure_retries")
    if (
        type(retries) is not int
        or retries < 0
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or timeout <= 0
        or profile.get("runs_per_case") != 1
    ):
        raise ValueError("Invalid WCXB execution budget profile")
    return providers


def paired_latency_methods(profile: dict, certificate: dict) -> dict[str, str]:
    """Join paired-call roles to certified implementations without browser names."""
    result = {}
    for method, key in (("release", "release_binary_sha256"), ("pr", "candidate_binary_sha256")):
        matches = [
            row["provider"]
            for row in certificate["providers"]
            if row.get("identity", {}).get("binary_sha256") == profile.get(key)
        ]
        if not profile.get(key) or len(matches) != 1 or matches[0] in result:
            raise ValueError("WCXB paired latency must bind distinct unique implementations")
        result[matches[0]] = method
    return result


def validate_execution(execution, bound, source_hash, sample_hash, case_count, profile):
    if (
        execution.get("run_id") != bound["run_id"]
        or execution.get("provider") != bound["provider"]
        or execution.get("implementation_identity") != bound["identity"]
        or execution.get("case_count") != case_count
        or execution.get("source_dataset_sha256") != source_hash
        or execution.get("selection_manifest", {}).get("sha256") != sample_hash
        or execution.get("timeout_seconds") != profile["timeout_seconds"]
        or execution.get("max_failure_retries") != profile["max_failure_retries"]
    ):
        raise ValueError("WCXB execution differs from certified inputs")


def validate_calls(logical, calls, bound, record, max_attempts):
    input_hash = hashlib.sha256(record["html"].encode()).hexdigest()
    reference_hash = hashlib.sha256(record["groundtruth_content"].encode()).hexdigest()
    for row in (logical, *calls):
        for key in ("provider", "run_id"):
            if row.get(key) != bound[key]:
                raise ValueError("WCXB call identity differs from certificate")
        if (
            row.get("input_html_sha256") != input_hash
            or row.get("reference_sha256") != reference_hash
        ):
            raise ValueError("WCXB call input differs from frozen source")
        safe_identifier(row["attempt_id"])
        if (
            type(row.get("ok")) is not bool
            or type(row.get("output_chars")) is not int
            or row["output_chars"] < 0
        ):
            raise ValueError("Invalid WCXB call outcome or output length")
        elapsed = row.get("elapsed_ms")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("Invalid WCXB call latency")
    total = logical.get("total_elapsed_ms")
    if (
        type(total) not in (int, float)
        or not math.isfinite(total)
        or total < 0
        or type(logical.get("physical_attempt_count")) is not int
        or not calls
        or len(calls) > max_attempts
        or len(calls) != logical["physical_attempt_count"]
        or any(type(row.get("physical_attempt")) is not int for row in calls)
        or [row.get("physical_attempt") for row in calls] != list(range(1, len(calls) + 1))
        or len({row["attempt_id"] for row in calls}) != len(calls)
        or abs(sum(row["elapsed_ms"] for row in calls) - total) > 0.02
        or logical.get("retry_selection") != "final_physical_attempt_is_authoritative"
    ):
        raise ValueError("WCXB retry or latency binding failed: " + logical["attempt_id"])
    final = calls[-1]
    for key in (
        "output_sha256",
        "output_chars",
        "ok",
        "status_code",
        "error_type",
        "error_message",
        "elapsed_ms",
    ):
        if logical.get(key) != final.get(key):
            raise ValueError("WCXB final physical call differs from logical result")


def physical_response(inputs: EvidenceInputs, run_dir: Path, call: dict) -> bytes:
    if call.get("physical_artifact_path"):
        path = contained_path(run_dir, call["physical_artifact_path"], reject_symlinks=True)
        raw = inputs.read(path)
    else:
        # Older certified empty responses can be reconstructed exactly, never guessed.
        raw = b"" if call["output_chars"] == 0 else b"\n" if call["output_chars"] == 1 else None
    if raw is None or hashlib.sha256(raw).hexdigest() != call["output_sha256"]:
        raise ValueError("WCXB physical response changed or cannot be materialized")
    if len(raw.decode("utf-8")) != call["output_chars"]:
        raise ValueError("WCXB physical output length differs")
    return raw


def install_scores(root: Path, payloads: dict[str, bytes | Path], unchanged) -> None:
    """Install only validated new outputs; undo handled failures without replacing files."""
    installed = []
    try:
        unchanged()
        for relative, payload in payloads.items():
            raw = payload.read_bytes() if isinstance(payload, Path) else payload
            path = contained_path(root, relative, reject_symlinks=True)
            atomic_create(path, raw)
            identity = path.stat()
            installed.append(
                (path, hashlib.sha256(raw).hexdigest(), identity.st_dev, identity.st_ino)
            )
        unchanged()
    except BaseException:
        for path, expected_hash, device, inode in reversed(installed):
            if path.is_symlink() or not path.is_file():
                continue
            identity = path.stat()
            if (identity.st_dev, identity.st_ino) == (device, inode) and digest(
                path
            ) == expected_hash:
                path.unlink()
        raise


def score(*, source: Path, sample_manifest: Path, evidence_root: Path, profile_path: Path) -> dict:
    """Score a complete declared cohort without changing its measured evidence."""
    with tempfile.TemporaryDirectory(prefix="wcxb-scores-") as work:
        return _score(
            source=source,
            sample_manifest=sample_manifest,
            evidence_root=evidence_root,
            profile_path=profile_path,
            staging=Path(work),
        )


def _score(*, source, sample_manifest, evidence_root, profile_path, staging):
    reject_evidence_symlinks(source / "test")
    if evidence_root.is_symlink():
        raise ValueError("WCXB evidence root must not be linked")
    inputs = EvidenceInputs()
    profile = json.loads(inputs.read(profile_path))
    providers = participant_names(profile)
    output = evidence_root / "wcxb-scores.jsonl"
    summary_path = evidence_root / "wcxb-score-summary.json"
    if any(path.exists() or path.is_symlink() for path in (output, summary_path)):
        raise ValueError("Refusing to overwrite WCXB scores")
    retained = {}
    certificate = json.loads(inputs.read(evidence_root / "certification.json"))
    records, source_hash = load_wcxb_source(source, manifest=sample_manifest)
    sample_bytes = inputs.read(sample_manifest)
    sample = json.loads(sample_bytes)
    selected, distribution = validate_manifest(records, source_hash, sample)
    sample_hash = hashlib.sha256(sample_bytes).hexdigest()
    if sample_hash != certificate.get("sample_sha256_before") or sample_hash != certificate.get(
        "sample_sha256_after"
    ):
        raise ValueError("WCXB manifest differs from the certified source-only selection")
    records = selected
    case_count = len(records)
    if not case_count or type(profile.get("case_count")) is not int:
        raise ValueError("WCXB scoring requires a nonempty integer task scope")
    exclusions = {row["file_id"]: row["reason"] for row in sample.get("exclusions", [])}
    quality_count = case_count - len(exclusions)
    if (
        certificate.get("complete") is not True
        or source_hash != certificate["source_sha256_before"]
        or source_hash != certificate["source_sha256_after"]
    ):
        raise ValueError("WCXB cohort is not certified against these frozen inputs")
    if [x["provider"] for x in certificate["providers"]] != providers:
        raise ValueError("WCXB provider matrix differs from the certificate")
    if (
        sample_hash != profile["sample_manifest_sha256"]
        or case_count != profile["case_count"]
        or sample.get("scope") != "qualified_test"
        or exclusions
        or source_hash != profile["source_dataset_sha256"]
        or sample["dataset_id"] != certificate.get("benchmark")
        or profile.get("primary_quality_case_count") != case_count
    ):
        raise ValueError("WCXB scoring must use the current qualified-case manifest")
    if profile['official_evaluator_sha256'] != UPSTREAM_EVALUATOR_SHA256:
        raise ValueError('WCXB profile names unsupported official metric provenance')
    evaluator_hash = UPSTREAM_EVALUATOR_SHA256
    scorer_hash = hashlib.sha256(inputs.read(Path(__file__))).hexdigest()
    ground_truth = {r['track_id']: {
        'main_content': r['groundtruth_content'],
        'page_type': r['meta']['page_type'],
        'with': r['ground_truth'].get('with') or [],
        'without': r['ground_truth'].get('without') or [],
    } for r in records}
    reference_audit = {
        "empty_main_content": sum(
            not bool(gt["main_content"].strip()) for gt in ground_truth.values()
        ),
        "with_absent_from_main_content": sum(
            s.lower() not in gt["main_content"].lower()
            for gt in ground_truth.values()
            for s in gt["with"]
        ),
        "without_present_in_main_content": sum(
            s.lower() in gt["main_content"].lower()
            for gt in ground_truth.values()
            for s in gt["without"]
        ),
    }
    by_id = {r["track_id"]: r for r in records}
    for case_id in by_id:
        safe_identifier(case_id)
    correction_root = evidence_root / "latency-correction"
    correction = None
    correction_methods = {}
    if bool(profile.get("latency_correction_receipt_sha256")) != correction_root.exists():
        raise ValueError("WCXB current profile and full-scope latency correction differ")
    if correction_root.exists():
        correction_methods = paired_latency_methods(profile, certificate)
        for name in ("receipt.json", "calls.jsonl", "certification.json"):
            inputs.read(contained_path(correction_root, name, reject_symlinks=True))
        correction_receipt, correction = load_full_paired_latency(
            correction_root, profile, set(by_id)
        )
        if digest(correction_root / "receipt.json") != profile["latency_correction_receipt_sha256"]:
            raise ValueError("WCXB corrected timing receipt differs from current profile")
        correction_certificate = json.loads((correction_root / "certification.json").read_text())
        if (
            correction_certificate.get("kind") != "certified_wcxb_paired_latency"
            or correction_certificate.get("complete") is not True
            or correction_certificate.get("case_count") != case_count
            or correction_certificate.get("physical_call_count") != case_count * 4
            or correction_certificate.get("selected_round") != 1
            or correction_certificate.get("source_sha256_before") != source_hash
            or correction_certificate.get("source_sha256_after") != source_hash
            or correction_certificate.get("sample_sha256_before") != sample_hash
            or correction_certificate.get("sample_sha256_after") != sample_hash
            or correction_certificate.get("calls_sha256") != correction_receipt["calls_sha256"]
            or correction_certificate.get("receipt_sha256")
            != digest(correction_root / "receipt.json")
        ):
            raise ValueError("WCXB paired latency certification differs from corrected evidence")
    summaries = {}
    with io.StringIO() as scored:
        for provider in providers:
            run_dir = contained_path(evidence_root, provider, reject_symlinks=True)
            bound = next(x for x in certificate["providers"] if x["provider"] == provider)
            result_bytes = inputs.read(run_dir / "results.jsonl")
            if hashlib.sha256(result_bytes).hexdigest() != bound["results_sha256"]:
                raise ValueError("Certified results changed: " + provider)
            rows = parse_jsonl(result_bytes)
            execution = json.loads(inputs.read(run_dir / "execution.json"))
            validate_execution(execution, bound, source_hash, sample_hash, case_count, profile)
            if len(rows) != case_count or {row["track_id"] for row in rows} != set(ground_truth):
                raise ValueError("Incomplete WCXB rows: " + provider)
            if len({row["attempt_id"] for row in rows}) != len(rows):
                raise ValueError("Duplicate WCXB logical attempt IDs")
            physical_file = run_dir / "physical-attempts.jsonl"
            physical_bytes = inputs.read(physical_file)
            physical = parse_jsonl(physical_bytes, skip_blank=True)
            if len({row["attempt_id"] for row in physical}) != len(physical):
                raise ValueError("Duplicate WCXB physical attempt IDs")
            by_case = {}
            for call in physical:
                by_case.setdefault(call["track_id"], []).append(call)
            if set(by_case) != set(ground_truth):
                raise ValueError("Incomplete WCXB physical calls: " + provider)
            retry_details = {}
            for logical in rows:
                calls = by_case[logical["track_id"]]
                record = by_id[logical["track_id"]]
                validate_calls(logical, calls, bound, record, profile["max_failure_retries"] + 1)
                method = correction_methods.get(provider)
                if method is not None:
                    measured = correction[logical["track_id"], method]
                    if measured["output_sha256"] != logical["output_sha256"]:
                        raise ValueError(
                            "WCXB corrected timing output differs from certified content: "
                            + logical["attempt_id"]
                        )
                # Validate final and non-final physical responses alike.
                for call in calls:
                    physical_response(inputs, run_dir, call)
                retry_details[logical["track_id"]] = []
                for call in calls[:-1]:
                    raw = physical_response(inputs, run_dir, call)
                    relative = (
                        f"physical-artifacts/{provider}/{logical['track_id']}"
                        f"-p{call['physical_attempt']:02d}.txt"
                    )
                    target = contained_path(evidence_root, relative, reject_symlinks=True)
                    if target.exists():
                        if inputs.read(target) != raw:
                            raise ValueError("Retained physical artifact changed: " + relative)
                    else:
                        staged = contained_path(staging, relative)
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        staged.write_bytes(raw)
                        retained[relative] = staged
                    retry_details[logical["track_id"]].append(
                        {
                            "physical_attempt": call["physical_attempt"],
                            "elapsed_ms": call["elapsed_ms"],
                            "error_type": call["error_type"],
                            "error_message": public_error(call["error_message"]),
                            "output_sha256": call["output_sha256"],
                            "artifact_path": relative,
                        }
                    )
            totals = Counter()
            quality_totals = Counter()
            diagnoses = Counter()
            latencies = []
            successful_latencies = []
            for row in rows:
                case_id = row["track_id"]
                gt = ground_truth[case_id]
                artifact = contained_path(run_dir, row["artifact_path"], reject_symlinks=True)
                artifact_bytes = inputs.read(artifact)
                if hashlib.sha256(artifact_bytes).hexdigest() != row["output_sha256"]:
                    raise ValueError("WCXB output artifact changed: " + row["attempt_id"])
                predicted = artifact_bytes.decode("utf-8")
                if len(predicted) != row["output_chars"]:
                    raise ValueError("WCXB logical output length differs")
                reference = gt["main_content"]
                precision, recall, f1 = word_f1(predicted, reference)
                with_rate = snippet_check(predicted, gt["with"])
                without_rate = snippet_check(predicted, gt["without"])
                if any(
                    not isinstance(value, Real)
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                    for value in (precision, recall, f1, with_rate, without_rate)
                ):
                    raise ValueError("WCXB evaluator returned invalid metrics")
                precision, recall, f1, with_rate, without_rate = map(
                    float, (precision, recall, f1, with_rate, without_rate)
                )
                diagnosis = classify(row, predicted, precision, recall, reference)
                diagnoses["excluded" if case_id in exclusions else diagnosis] += 1
                if case_id not in exclusions:
                    for key, value in (
                        ("precision", precision),
                        ("recall", recall),
                        ("f1", f1),
                        ("with_rate", with_rate),
                        ("without_rate", without_rate),
                    ):
                        quality_totals[key] += value
                for key, value in (
                    ("precision", precision),
                    ("recall", recall),
                    ("f1", f1),
                    ("with_rate", with_rate),
                    ("without_rate", without_rate),
                ):
                    totals[key] += value
                totals["delivered"] += bool(row["ok"] and predicted.strip())
                corrected_elapsed = (
                    correction[case_id, correction_methods[provider]]["elapsed_ms"]
                    if provider in correction_methods
                    else row["total_elapsed_ms"]
                )
                totals["latency_ms"] += corrected_elapsed
                latencies.append(corrected_elapsed)
                if row["ok"] and predicted.strip():
                    successful_latencies.append(corrected_elapsed)
                if reference.strip():
                    totals["nonempty_reference_count"] += 1
                    totals["nonempty_reference_f1"] += f1
                scored.write(
                    json.dumps(
                        {
                            "provider": provider,
                            "case_id": case_id,
                            "page_type": gt["page_type"],
                            "original_url": by_id[case_id]["url"],
                            "attempt_id": row["attempt_id"],
                            "run_id": row["run_id"],
                            "ok": row["ok"],
                            "status_code": row.get("status_code"),
                            "error_type": row["error_type"],
                            "error_message": public_error(row["error_message"]),
                            "output_sha256": row["output_sha256"],
                            "input_html_sha256": row["input_html_sha256"],
                            "reference_sha256": row["reference_sha256"],
                            "output_chars": row["output_chars"],
                            "total_elapsed_ms": corrected_elapsed,
                            "prior_total_elapsed_ms": row["total_elapsed_ms"]
                            if provider in correction_methods
                            else None,
                            "latency_source": "paired_final_round"
                            if provider in correction_methods
                            else "certified_call",
                            "delivered": bool(row["ok"] and predicted.strip()),
                            "physical_attempt_count": row["physical_attempt_count"],
                            "retry_failures": retry_details[case_id],
                            "precision": precision,
                            "recall": recall,
                            "f1": f1,
                            "with_rate": with_rate,
                            "without_rate": without_rate,
                            "reference_empty": not bool(reference.strip()),
                            "diagnosis": diagnosis,
                            "quality_eligible": case_id not in exclusions,
                            "exclusion_reason": exclusions.get(case_id),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            summaries[provider] = {
                "cases": case_count,
                "quality_cases": quality_count,
                "delivered": totals["delivered"],
                "mean_latency_ms": totals["latency_ms"] / case_count,
                "p50_latency_ms": percentile(latencies, 0.5),
                "p95_latency_ms": percentile(latencies, 0.95),
                "success_latency_count": len(successful_latencies),
                "success_mean_latency_ms": sum(successful_latencies) / len(successful_latencies)
                if successful_latencies
                else None,
                "success_p50_latency_ms": percentile(successful_latencies, 0.5)
                if successful_latencies
                else None,
                "success_p95_latency_ms": percentile(successful_latencies, 0.95)
                if successful_latencies
                else None,
                "nonempty_reference_count": totals["nonempty_reference_count"],
                "nonempty_reference_f1": totals["nonempty_reference_f1"]
                / totals["nonempty_reference_count"],
                "diagnoses": dict(diagnoses),
                "physical_attempts_sha256": hashlib.sha256(physical_bytes).hexdigest(),
                "physical_call_count": len(physical),
                **{
                    key: totals[key] / case_count
                    for key in ("precision", "recall", "f1", "with_rate", "without_rate")
                },
                **{
                    "quality_" + key: quality_totals[key] / quality_count
                    for key in ("precision", "recall", "f1", "with_rate", "without_rate")
                },
            }
        score_bytes = scored.getvalue().encode("utf-8")
    summary = {
        "benchmark": sample["dataset_id"],
        "source_sha256": source_hash,
        "sample_sha256": sample_hash,
        "distribution": distribution,
        "case_count": case_count,
        "quality_case_count": quality_count,
        "exclusions": sample.get("exclusions", []),
        "reference_audit": reference_audit,
        "official_evaluator_sha256": evaluator_hash,
        "scorer_sha256": scorer_hash,
        "providers": summaries,
    }

    def unchanged():
        inputs.check()
        reject_evidence_symlinks(source / "test")
        if load_wcxb_source(source, manifest=sample_manifest)[1] != source_hash:
            raise ValueError("WCXB source changed during scoring")

    unchanged()
    install_scores(
        evidence_root,
        {
            **retained,
            "wcxb-scores.jsonl": score_bytes,
            "wcxb-score-summary.json": json_bytes(summary),
        },
        unchanged,
    )
    return summary


def main(*, root: Path | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--sample-manifest", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument(
        "--profile", type=Path, required=True
    )
    args = parser.parse_args()
    args.source, args.sample_manifest = selected_wcxb_input(args.source, args.sample_manifest)
    score(
        source=args.source,
        sample_manifest=args.sample_manifest,
        evidence_root=args.evidence_root,
        profile_path=args.profile,
    )
    print(args.evidence_root / "wcxb-score-summary.json")
