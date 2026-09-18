"""Score a certified WCXB cohort with the pinned official evaluator."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_webmainbench_benchmark import WCXB_PROVIDERS, load_wcxb_source
from scripts.select_wcxb_sample import validate_manifest
from harness.web_fetch.safety import contained_path
from harness.web_fetch.wcxb_latency import load_full_paired_latency


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_error(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r'/(?:Users|home|private/var)/[^\s:]+', '[local path]', value)


def official_evaluator(source: Path, expected_sha256: str):
    module_path = source / "evaluate.py"
    evaluator_hash = digest(module_path)
    if evaluator_hash != expected_sha256:
        raise ValueError("WCXB official evaluator hash differs from the current profile")
    spec = importlib.util.spec_from_file_location("wcxb_official_evaluate", module_path)
    if spec is None or spec.loader is None:
        raise ValueError("WCXB official evaluator unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, evaluator_hash


def classify(row: dict, predicted: str, precision: float, recall: float, reference: str) -> str:
    if not row["ok"]:
        return "request_error"
    if not predicted.strip():
        return "empty_output"
    if not reference.strip():
        return "empty_reference"
    lowered = predicted[:2000].lower()
    if any(value in lowered for value in ("this site can't be reached", "404 not found", "bad gateway")) and recall < .05:
        return "error_page"
    if precision < .25 and recall < .25:
        return "low_precision_and_recall"
    if precision < .25:
        return "low_precision"
    if recall < .25:
        return "low_recall"
    return "none"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    certificate = json.loads((args.evidence_root / "certification.json").read_text())
    records, source_hash = load_wcxb_source(args.source)
    sample_bytes = args.sample_manifest.read_bytes()
    sample = json.loads(sample_bytes)
    selected, distribution = validate_manifest(records, source_hash, sample)
    sample_hash = hashlib.sha256(sample_bytes).hexdigest()
    if (sample_hash != certificate.get('sample_sha256_before')
            or sample_hash != certificate.get('sample_sha256_after')):
        raise ValueError('WCXB manifest differs from the certified source-only selection')
    records = selected
    case_count = len(records)
    exclusions = {row['file_id']: row['reason'] for row in sample.get('exclusions', [])}
    quality_count = case_count - len(exclusions)
    if not certificate.get("complete") or source_hash != certificate["source_sha256_before"] or source_hash != certificate["source_sha256_after"]:
        raise ValueError("WCXB cohort is not certified against these frozen inputs")
    if [x["provider"] for x in certificate["providers"]] != list(WCXB_PROVIDERS):
        raise ValueError("WCXB provider matrix differs from the certificate")
    profile = json.loads((Path(__file__).resolve().parents[1] / 'evaluation/wcxb/current-report.json').read_text())
    if (sample_hash != profile['sample_manifest_sha256'] or case_count != profile['case_count']
            or sample.get('scope') != 'qualified_test' or exclusions):
        raise ValueError('WCXB scoring must use the current qualified-case manifest')
    evaluator, evaluator_hash = official_evaluator(args.source, profile['official_evaluator_sha256'])
    ground_truth = evaluator.load_ground_truth("test")
    if not {r["track_id"] for r in records} <= set(ground_truth):
        raise ValueError("Official evaluator is missing selected cases")
    ground_truth = {r['track_id']: ground_truth[r['track_id']] for r in records}
    reference_audit = {
        'empty_main_content': sum(not bool(gt['main_content'].strip()) for gt in ground_truth.values()),
        'with_absent_from_main_content': sum(s.lower() not in gt['main_content'].lower()
                                            for gt in ground_truth.values() for s in gt['with']),
        'without_present_in_main_content': sum(s.lower() in gt['main_content'].lower()
                                               for gt in ground_truth.values() for s in gt['without']),
    }
    by_id = {r["track_id"]: r for r in records}
    correction_root = args.evidence_root / 'latency-correction'
    correction = None
    if bool(profile.get('latency_correction_receipt_sha256')) != correction_root.exists():
        raise ValueError('WCXB current profile and full-scope latency correction differ')
    if correction_root.exists():
        correction_receipt, correction = load_full_paired_latency(correction_root, profile, set(by_id))
        if digest(correction_root / 'receipt.json') != profile['latency_correction_receipt_sha256']:
            raise ValueError('WCXB corrected timing receipt differs from current profile')
        correction_certificate = json.loads((correction_root / 'certification.json').read_text())
        if (correction_certificate.get('kind') != 'certified_wcxb_paired_latency'
                or correction_certificate.get('complete') is not True
                or correction_certificate.get('case_count') != case_count
                or correction_certificate.get('physical_call_count') != case_count * 4
                or correction_certificate.get('selected_round') != 1
                or correction_certificate.get('source_sha256_before') != source_hash
                or correction_certificate.get('source_sha256_after') != source_hash
                or correction_certificate.get('sample_sha256_before') != sample_hash
                or correction_certificate.get('sample_sha256_after') != sample_hash
                or correction_certificate.get('calls_sha256') != correction_receipt['calls_sha256']
                or correction_certificate.get('receipt_sha256') != digest(correction_root / 'receipt.json')):
            raise ValueError('WCXB paired latency certification differs from corrected evidence')
    output = args.evidence_root / "wcxb-scores.jsonl"
    summary_path = args.evidence_root / "wcxb-score-summary.json"
    if output.exists() or summary_path.exists():
        raise ValueError("Refusing to overwrite WCXB scores")
    summaries = {}
    with output.open("w", encoding="utf-8") as scored:
        for provider in WCXB_PROVIDERS:
            run_dir = args.evidence_root / provider
            bound = next(x for x in certificate["providers"] if x["provider"] == provider)
            if digest(run_dir / "results.jsonl") != bound["results_sha256"]:
                raise ValueError("Certified results changed: " + provider)
            rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()]
            if len(rows) != case_count or {row["track_id"] for row in rows} != set(ground_truth):
                raise ValueError("Incomplete WCXB rows: " + provider)
            physical_file = run_dir / 'physical-attempts.jsonl'
            physical = [json.loads(line) for line in physical_file.read_text().splitlines() if line]
            by_case = {}
            for call in physical:
                by_case.setdefault(call['track_id'], []).append(call)
            if set(by_case) != set(ground_truth):
                raise ValueError('Incomplete WCXB physical calls: ' + provider)
            empty_sha = hashlib.sha256(b'').hexdigest()
            newline_sha = hashlib.sha256(b'\n').hexdigest()
            retry_details = {}
            for logical in rows:
                calls = by_case[logical['track_id']]
                if (len(calls) != logical['physical_attempt_count']
                        or abs(sum(x['elapsed_ms'] for x in calls) - logical['total_elapsed_ms']) > .02
                        or calls[-1]['output_sha256'] != logical['output_sha256']):
                    raise ValueError('WCXB retry or latency binding failed: ' + logical['attempt_id'])
                if correction is not None and provider in ('moli', 'moli-pr643'):
                    method = 'release' if provider == 'moli' else 'pr'
                    measured = correction[logical['track_id'], method]
                    if measured['output_sha256'] != logical['output_sha256']:
                        raise ValueError('WCXB corrected timing output differs from certified content: ' + logical['attempt_id'])
                retry_details[logical['track_id']] = []
                for call in calls[:-1]:
                    if call.get('physical_artifact_path'):
                        physical_artifact = contained_path(run_dir, call['physical_artifact_path'])
                        if physical_artifact.is_symlink() or digest(physical_artifact) != call['output_sha256']:
                            raise ValueError('WCXB physical response changed: ' + call['attempt_id'])
                        raw = physical_artifact.read_bytes()
                    else:
                        raw = (b'' if call['output_chars'] == 0 and call['output_sha256'] == empty_sha
                               else b'\n' if call['output_chars'] == 1 and call['output_sha256'] == newline_sha
                               else None)
                    if raw is None:
                        raise ValueError('A non-final physical response cannot be materialized from its retained hash')
                    relative = f'physical-artifacts/{provider}/{logical["track_id"]}-p{call["physical_attempt"]:02d}.txt'
                    target = args.evidence_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() and target.read_bytes() != raw:
                        raise ValueError('Retained physical artifact changed: ' + relative)
                    target.write_bytes(raw)
                    if digest(target) != call['output_sha256']:
                        raise ValueError('Physical artifact hash mismatch: ' + relative)
                    retry_details[logical['track_id']].append({
                        'physical_attempt': call['physical_attempt'], 'elapsed_ms': call['elapsed_ms'],
                        'error_type': call['error_type'], 'error_message': public_error(call['error_message']),
                        'output_sha256': call['output_sha256'], 'artifact_path': relative,
                    })
            totals = Counter()
            quality_totals = Counter()
            diagnoses = Counter()
            latencies = []
            successful_latencies = []
            for row in rows:
                case_id = row["track_id"]
                gt = ground_truth[case_id]
                artifact = run_dir / row["artifact_path"]
                if artifact.is_symlink() or digest(artifact) != row["output_sha256"]:
                    raise ValueError("Output artifact changed: " + row["attempt_id"])
                predicted = artifact.read_text(encoding="utf-8")
                reference = gt["main_content"]
                precision, recall, f1 = evaluator.word_f1(predicted, reference)
                with_rate = evaluator.snippet_check(predicted, gt["with"])
                without_rate = evaluator.snippet_check(predicted, gt["without"])
                diagnosis = classify(row, predicted, precision, recall, reference)
                diagnoses['excluded' if case_id in exclusions else diagnosis] += 1
                if case_id not in exclusions:
                    for key, value in (("precision", precision), ("recall", recall), ("f1", f1),
                                       ("with_rate", with_rate), ("without_rate", without_rate)):
                        quality_totals[key] += value
                for key, value in (("precision", precision), ("recall", recall), ("f1", f1),
                                   ("with_rate", with_rate), ("without_rate", without_rate)):
                    totals[key] += value
                totals["delivered"] += bool(row["ok"] and predicted.strip())
                corrected_elapsed = (correction[case_id, 'release' if provider == 'moli' else 'pr']['elapsed_ms']
                                     if correction is not None and provider in ('moli', 'moli-pr643')
                                     else row['total_elapsed_ms'])
                totals["latency_ms"] += corrected_elapsed
                latencies.append(corrected_elapsed)
                if row["ok"] and predicted.strip():
                    successful_latencies.append(corrected_elapsed)
                if reference.strip():
                    totals["nonempty_reference_count"] += 1
                    totals["nonempty_reference_f1"] += f1
                scored.write(json.dumps({
                    "provider": provider, "case_id": case_id,
                    "page_type": gt["page_type"], "original_url": by_id[case_id]["url"],
                    "attempt_id": row["attempt_id"], "run_id": row["run_id"],
                    "ok": row["ok"], "status_code": row.get("status_code"), "error_type": row["error_type"],
                    "error_message": public_error(row["error_message"]), "output_sha256": row["output_sha256"],
                    "input_html_sha256": row["input_html_sha256"], "reference_sha256": row["reference_sha256"],
                    "output_chars": row["output_chars"], "total_elapsed_ms": corrected_elapsed,
                    "prior_total_elapsed_ms": row['total_elapsed_ms'] if correction is not None and provider in ('moli', 'moli-pr643') else None,
                    "latency_source": 'paired_final_round' if correction is not None and provider in ('moli', 'moli-pr643') else 'certified_call',
                    "delivered": bool(row["ok"] and predicted.strip()),
                    "physical_attempt_count": row["physical_attempt_count"],
                    "retry_failures": retry_details[case_id],
                    "precision": precision, "recall": recall, "f1": f1,
                    "with_rate": with_rate, "without_rate": without_rate,
                    "reference_empty": not bool(reference.strip()), "diagnosis": diagnosis,
                    "quality_eligible": case_id not in exclusions,
                    "exclusion_reason": exclusions.get(case_id),
                }, ensure_ascii=False) + "\n")
            summaries[provider] = {
                "cases": case_count, "quality_cases": quality_count, "delivered": totals["delivered"],
                "mean_latency_ms": totals["latency_ms"] / case_count,
                "p50_latency_ms": percentile(latencies, .5),
                "p95_latency_ms": percentile(latencies, .95),
                "success_latency_count": len(successful_latencies),
                "success_mean_latency_ms": sum(successful_latencies) / len(successful_latencies) if successful_latencies else None,
                "success_p50_latency_ms": percentile(successful_latencies, .5) if successful_latencies else None,
                "success_p95_latency_ms": percentile(successful_latencies, .95) if successful_latencies else None,
                "nonempty_reference_count": totals["nonempty_reference_count"],
                "nonempty_reference_f1": totals["nonempty_reference_f1"] / totals["nonempty_reference_count"],
                "diagnoses": dict(diagnoses),
                "physical_attempts_sha256": digest(physical_file),
                "physical_call_count": len(physical),
                **{key: totals[key] / case_count for key in ("precision", "recall", "f1", "with_rate", "without_rate")},
                **{'quality_' + key: quality_totals[key] / quality_count for key in
                   ("precision", "recall", "f1", "with_rate", "without_rate")},
            }
    summary_path.write_text(json.dumps({"benchmark": sample['dataset_id'], "source_sha256": source_hash,
                                        "sample_sha256": sample_hash, "distribution": distribution,
                                        "case_count": case_count, "quality_case_count": quality_count,
                                        "exclusions": sample.get('exclusions', []),
                                        "reference_audit": reference_audit,
                                        "official_evaluator_sha256": evaluator_hash,
                                        "providers": summaries}, ensure_ascii=False, indent=2) + "\n")
    print(summary_path)


if __name__ == "__main__":
    main()
