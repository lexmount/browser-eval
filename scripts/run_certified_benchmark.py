#!/usr/bin/env python3
"""Run one immutable benchmark cohort with mandatory pre/post reference checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.record_io import load_jsonl
from harness.web_fetch.reference_inputs import read_dataset_bytes


def _browser_contract(case: dict) -> bool:
    """Return whether the declared reference was produced from browser DOM.

    This is deliberately based on the capture contract rather than case IDs.
    The two values are the canonical methods emitted by the reference freezer.
    """
    method = case.get("source_verification", {}).get("method")
    return method in {
        "Independent visible browser DOM; complete declared scope",
        "Independent rendered browser DOM; complete declared scope",
    }


def browser_observation_cases(dataset: dict, captures: Path,
                              selected_case_ids: list[str]) -> list[str]:
    """Detect selected cases whose current independent capture needs a browser.

    Browser observations are required when the task contract declares a
    browser-DOM reference, or when the just-completed independent HTTP capture
    cannot supply the facts/reference file that the freezer consumes.
    Malformed or missing audit rows fail closed instead of being guessed at.
    """
    from harness.web_fetch.metrics import normalize_for_match
    from harness.web_fetch.reference_lifecycle import requires_fresh_reference

    try:
        rows = json.loads((captures / "audit.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Independent capture audit is unavailable") from error
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Independent capture audit is malformed")
    by_id: dict[str, dict] = {}
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or case_id in by_id:
            raise ValueError("Independent capture audit has missing or duplicate case IDs")
        by_id[case_id] = row

    cases = {case["id"]: case for case in dataset["cases"]}
    browser_cases = []
    for case_id in selected_case_ids:
        case = cases[case_id]
        if not requires_fresh_reference(case):
            continue
        row = by_id.get(case_id)
        if row is None:
            raise ValueError("Independent capture audit is missing case: " + case_id)
        reference = captures / case_id / "reference.txt"
        http_usable = row.get("status") == 200 and reference.is_file()
        if http_usable:
            try:
                normalized = normalize_for_match(reference.read_text(encoding="utf-8"))
            except OSError:
                http_usable = False
            else:
                http_usable = all(
                    any(normalize_for_match(value) in normalized
                        for value in fact["alternatives"])
                    for fact in case.get("required_facts", [])
                )
        if _browser_contract(case) or not http_usable:
            browser_cases.append(case_id)
    return browser_cases


def validate_browser_observations(path: Path, required_case_ids: list[str]) -> None:
    """Reject partial or unsuccessful browser batches before lifecycle use."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Browser observation batch is unavailable") from error
    if not isinstance(rows, dict) or set(rows) != set(required_case_ids):
        raise ValueError("Browser observation batch does not match required case scope")
    failed = [
        case_id for case_id in required_case_ids
        if not isinstance(rows[case_id], dict)
        or rows[case_id].get("case_id") != case_id
        or rows[case_id].get("status") != "success"
        or rows[case_id].get("completion", {}).get("satisfied") is not True
    ]
    if failed:
        raise ValueError(
            "Required browser observations failed: " + ", ".join(failed))


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> None:
    if (argv or sys.argv[1:])[:1] == ["risk-control"]:
        from scripts.run_risk_control_moli import main as risk_control_main
        risk_control_main((argv or sys.argv[1:])[1:], certified=True)
        return
    if (argv or sys.argv[1:])[:1] == ["webmainbench-correction"]:
        from scripts.certify_webmainbench_case_correction import main as correction_main
        correction_main((argv or sys.argv[1:])[1:])
        return
    if (argv or sys.argv[1:])[:1] == ["webmainbench-full"]:
        from harness.web_fetch.webmainbench_certification import main as webmainbench_full_main
        webmainbench_full_main((argv or sys.argv[1:])[1:])
        return
    if (argv or sys.argv[1:])[:1] == ["wcxb"]:
        run_wcxb((argv or sys.argv[1:])[1:])
        return
    if (argv or sys.argv[1:])[:1] == ["wcxb-latency"]:
        certify_wcxb_latency((argv or sys.argv[1:])[1:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--providers", required=True)
    parser.add_argument("--case-ids", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--capture-workers", type=int, default=8)
    parser.add_argument("--browser-capture-attempts", type=int, default=2)
    parser.add_argument("--browser-binary")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--provider-config", type=Path,help="Instance-specific provider settings passed unchanged to the shared runner")
    parser.add_argument("--reference-promotion", type=Path)
    parser.add_argument('--retrieval-mode', choices=('declared', 'native'), default='declared',
                        help='Explicitly select ordinary native fetch instead of selector-guided dynamic execution')
    parser.add_argument('--diagnostic', action='store_true',
                        help='Retain an explicitly uncertified cohort when stable rules have no live source qualification')
    args = parser.parse_args(argv)
    if args.provider_config and not args.provider_config.is_file():
        parser.error('--provider-config must be an existing file')
    if args.browser_capture_attempts < 1:
        parser.error("--browser-capture-attempts must be at least 1")

    case_ids = [value.strip() for value in args.case_ids.split(",") if value.strip()]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        parser.error("--case-ids must contain nonempty unique IDs")
    dataset = json.loads(read_dataset_bytes(args.dataset))
    known = {case["id"] for case in dataset["cases"]}
    if set(case_ids) - known:
        parser.error("Unknown case IDs: " + ", ".join(sorted(set(case_ids) - known)))
    if args.output.exists() or args.work_root.exists():
        parser.error("Output and work root must both be new immutable paths")

    # Stable rules deliberately bypass exact-reference lifecycle checks. A
    # completed wrapper therefore cannot certify that their old labels or
    # record requirements are still present on the live site.
    from harness.web_fetch.reference_lifecycle import source_unqualified_contract_cases
    unqualified = source_unqualified_contract_cases(
        case for case in dataset['cases'] if case['id'] in case_ids)
    if unqualified and not args.diagnostic:
        parser.error('Stable live-page contracts have no fresh source qualification; '
                     'use --diagnostic to retain uncertified evidence, not to waive scoring: '
                     + ', '.join(unqualified))

    from harness.web_fetch.retrieval_hints import native_fetch_dataset, parse_retrieval_hints
    if args.retrieval_mode == 'native':
        original_sha = hashlib.sha256(read_dataset_bytes(args.dataset)).hexdigest()
        dataset = native_fetch_dataset(dataset)
        dataset['comparison']['input_dataset_sha256'] = original_sha
    if args.diagnostic:
        dataset.setdefault('comparison',{}).update(
            evidence_state='diagnostic', publication_state='diagnostic',
            unqualified_reference_cases=sorted(unqualified))
    # Reject malformed execution contracts before doing any external captures.
    for case in dataset['cases']:
        parse_retrieval_hints(case.get('track', 'web'), case.get('retrieval_hints'), identifier=case['id'])
    if args.retrieval_mode == 'native' or args.diagnostic:
        args.work_root.mkdir(parents=True)
        args.dataset = args.work_root / 'input-dataset.json'
        args.dataset.write_text(json.dumps(dataset, ensure_ascii=False, indent=2)+'\n')

    python = sys.executable
    selected = ",".join(case_ids)
    pre_captures = args.work_root / "pre-captures"
    frozen = args.work_root / "frozen"
    pre_browser = args.work_root / "pre-browser.json"
    post_captures = args.work_root / "post-captures"
    post_browser = args.work_root / "post-browser.json"

    run([python, "scripts/collect_source_evidence.py", str(args.dataset),
         str(pre_captures), "--workers", str(args.capture_workers), "--only", selected])
    try:
        pre_browser_cases = browser_observation_cases(dataset, pre_captures, case_ids)
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    if pre_browser_cases and not args.browser_binary:
        parser.error(
            "--browser-binary is required for cases needing independent browser "
            "observations after pre-capture: " + ", ".join(pre_browser_cases))
    browser_args: list[str] = []
    if pre_browser_cases:
        run([python, "scripts/capture_headless_browser_observations.py",
             "--dataset", str(args.dataset), "--output", str(pre_browser),
             "--case-ids", ",".join(pre_browser_cases), "--browser-binary", args.browser_binary,
             "--attempts", str(args.browser_capture_attempts)])
        try:
            validate_browser_observations(pre_browser, pre_browser_cases)
        except ValueError as error:
            parser.error(str(error))
        browser_args = ["--browser-observations", str(pre_browser)]
    promotion_args = (["--reference-promotion", str(args.reference_promotion)]
                      if args.reference_promotion else [])
    run([python, "scripts/freeze_headless_references.py", "--captures", str(pre_captures),
         "--dataset", str(args.dataset), "--output", str(frozen),
         "--case-ids", selected, *browser_args, *promotion_args])

    benchmark = [python, "scripts/run_benchmark.py", "--dataset", str(frozen / "dataset.json"),
                 "--output", str(args.output), "--providers", args.providers,
                 "--case-ids", selected, "--workers", str(args.workers), "--measure-only"]
    if args.env_file:
        benchmark.extend(["--env-file", str(args.env_file)])
    if args.provider_config:
        benchmark.extend(["--provider-config", str(args.provider_config)])
    run(benchmark)

    # Re-capture HTTP evidence after every provider call has completed, then
    # choose browser fallbacks from that post-call evidence. Reusing the
    # pre-call browser inventory is unsafe: an endpoint can become unavailable
    # during a long cohort even though its frozen pre-capture was usable.
    run([python, "scripts/collect_source_evidence.py", str(args.output / "dataset.json"),
         str(post_captures), "--workers", str(args.capture_workers), "--only", selected])
    post_dataset = json.loads((args.output / "dataset.json").read_text(encoding="utf-8"))
    try:
        post_browser_cases = browser_observation_cases(
            post_dataset, post_captures, case_ids)
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    if post_browser_cases and not args.browser_binary:
        parser.error(
            "--browser-binary is required for cases needing independent browser "
            "observations after post-capture: " + ", ".join(post_browser_cases))
    post_browser_args: list[str] = []
    if post_browser_cases:
        run([python, "scripts/capture_headless_browser_observations.py",
             "--dataset", str(args.output / "dataset.json"), "--output", str(post_browser),
             "--case-ids", ",".join(post_browser_cases), "--browser-binary", args.browser_binary,
             "--attempts", str(args.browser_capture_attempts)])
        try:
            validate_browser_observations(post_browser, post_browser_cases)
        except ValueError as error:
            parser.error(str(error))
        post_browser_args = ["--browser-observations", str(post_browser)]
    run([python, "scripts/postcheck_headless.py", "--run-dir", str(args.output),
         "--captures", str(post_captures), "--resume", *post_browser_args])
    observations = post_captures / "observations.json"
    if not observations.exists():
        observations.parent.mkdir(parents=True, exist_ok=True)
        observations.write_text("[]\n", encoding="utf-8")
    run([python, "scripts/finalize_benchmark_run.py", "--run-dir", str(args.output),
         "--observations", str(observations)])
    print("Diagnostic benchmark cohort:" if args.diagnostic else "Certified benchmark cohort:", args.output)


def run_wcxb(argv: list[str]) -> None:
    """Certify a complete WCXB matrix, reusing unchanged certified methods."""
    parser = argparse.ArgumentParser(description="Certify WCXB test cohort")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--reuse-certified-root", type=Path,
                        help="Reuse unchanged, hash-bound methods from a prior complete cohort")
    parser.add_argument("--replace-providers", default="",
                        help="Comma-separated methods to rerun from the prior certified cohort")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from harness.web_fetch.webmainbench_runner import WCXB_PROVIDERS, load_wcxb_source

    if args.output_root.exists():
        parser.error("WCXB output root must be new and immutable")
    source_records, before = load_wcxb_source(args.dataset)
    from harness.web_fetch.wcxb_dataset import validate_manifest
    manifest = json.loads(args.sample_manifest.read_text())
    profile = json.loads((ROOT / 'evaluation/wcxb/current-report.json').read_text())
    if (profile.get('comparison_kind') != 'wcxb_frozen_html_to_markdown'
            or profile.get('providers') != list(WCXB_PROVIDERS)
            or hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest() != profile['sample_manifest_sha256']
            or manifest.get('scope') != 'qualified_test'
            or manifest.get('size') != profile['case_count']):
        raise ValueError('WCXB certified run must use the current qualified-case manifest')
    selected, _ = validate_manifest(source_records, before, manifest)
    expected_ids = {row['track_id'] for row in selected}
    sample_before = hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest()
    replace = set(filter(None, args.replace_providers.split(',')))
    if replace and not args.reuse_certified_root:
        parser.error('--replace-providers requires --reuse-certified-root')
    if not replace <= set(WCXB_PROVIDERS):
        parser.error('Unknown WCXB replacement provider')
    old_runs = []
    if args.reuse_certified_root:
        prior = args.reuse_certified_root.resolve()
        if prior == args.output_root.resolve():
            raise ValueError("WCXB prior and new evidence roots must differ")
        old_certificate = json.loads((prior / 'certification.json').read_text())
        old_runs = old_certificate.get('providers', [])
        old_names = [item['provider'] for item in old_runs]
        if (not old_certificate.get('complete') or not old_names
                or old_names != list(WCXB_PROVIDERS[:len(old_names)])
                or (len(old_names) >= len(WCXB_PROVIDERS) and not replace)
                or old_certificate['source_sha256_before'] != before
                or old_certificate['source_sha256_after'] != before
                or old_certificate['sample_sha256_before'] != sample_before
                or old_certificate['sample_sha256_after'] != sample_before):
            raise ValueError("WCXB reusable cohort is incomplete or differs from frozen inputs")
        for item in old_runs:
            provider = item['provider']
            old_dir = prior / provider
            if old_dir.is_symlink() or hashlib.sha256((old_dir / 'results.jsonl').read_bytes()).hexdigest() != item['results_sha256']:
                raise ValueError("WCXB prior result identity changed: " + provider)
            if any(path.is_symlink() for path in old_dir.rglob('*')):
                raise ValueError("WCXB prior evidence contains a symlink: " + provider)
            execution = json.loads((old_dir / 'execution.json').read_text())
            rows = load_jsonl(old_dir / 'results.jsonl')
            if (execution['run_id'] != item['run_id'] or execution['implementation_identity'] != item['identity']
                    or execution['case_count'] != len(selected) or execution['source_dataset_sha256'] != before
                    or execution['selection_manifest']['sha256'] != sample_before
                    or len(rows) != len(selected) or {row['track_id'] for row in rows} != expected_ids):
                raise ValueError("WCXB reusable provider is incomplete: " + provider)
            if provider not in replace:
                expected_binary = {'moli': profile['release_binary_sha256'],
                                   'moli-pr643': profile['candidate_binary_sha256']}.get(provider)
                if expected_binary and item['identity'].get('binary_sha256') != expected_binary:
                    raise ValueError('WCXB reused binary differs from the pinned build: ' + provider)
    if replace - {item['provider'] for item in old_runs}:
        parser.error('Replacement method is absent from the prior certified cohort')
    args.output_root.mkdir(parents=True)
    runs = []
    reusable = {item['provider']: item for item in old_runs if item['provider'] not in replace}
    for provider in WCXB_PROVIDERS:
        if provider in reusable:
            shutil.copytree(args.reuse_certified_root / provider, args.output_root / provider, symlinks=False)
            runs.append(reusable[provider])
            continue
        output = args.output_root / provider
        run([sys.executable, "scripts/run_webmainbench_benchmark.py", "--benchmark", "wcxb",
             "--dataset", str(args.dataset), "--provider", provider,
             "--output", str(output), "--env-file", str(args.env_file),
             "--case-ids-file", str(args.sample_manifest)])
        execution = json.loads((output / "execution.json").read_text(encoding="utf-8"))
        expected_binary = {'moli': profile['release_binary_sha256'],
                           'moli-pr643': profile['candidate_binary_sha256']}.get(provider)
        if expected_binary and execution['implementation_identity'].get('binary_sha256') != expected_binary:
            raise ValueError('WCXB binary differs from the pinned build: ' + provider)
        if (execution["case_count"] != len(selected) or execution["source_dataset_sha256"] != before
                or execution["selection_manifest"]["sha256"] != sample_before):
            raise ValueError("Incomplete or drifted WCXB provider cohort: " + provider)
        rows = load_jsonl(output / "results.jsonl")
        if len(rows) != len(selected) or {row["track_id"] for row in rows} != expected_ids:
            raise ValueError("Incomplete WCXB result matrix: " + provider)
        runs.append({"provider": provider, "run_id": execution["run_id"],
                     "identity": execution["implementation_identity"],
                     "results_sha256": hashlib.sha256((output / "results.jsonl").read_bytes()).hexdigest()})
    _, after = load_wcxb_source(args.dataset)
    sample_after = hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest()
    if after != before or sample_after != sample_before:
        raise ValueError("WCXB references changed during provider calls")
    receipt = {"schema_version": 1, "benchmark": manifest['dataset_id'],
               "source_sha256_before": before, "source_sha256_after": after,
               "sample_sha256_before": sample_before, "sample_sha256_after": sample_after,
               "providers": runs, "complete": True,
               "reused_provider_count": len(reusable)}
    (args.output_root / "certification.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Certified WCXB cohort:", args.output_root)


def certify_wcxb_latency(argv: list[str]) -> None:
    """Bind the complete paired timing calls to frozen inputs and binaries."""
    parser = argparse.ArgumentParser(description='Certify a complete WCXB paired latency cohort')
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--sample-manifest', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from harness.web_fetch.wcxb_dataset import validate_manifest
    from harness.web_fetch.wcxb_latency import load_full_paired_latency
    from harness.web_fetch.webmainbench_runner import load_wcxb_source

    certificate_path = args.evidence_root / 'certification.json'
    if certificate_path.exists():
        parser.error('paired latency cohort is already certified')
    profile = json.loads((ROOT / 'evaluation/wcxb/current-report.json').read_text())
    records, source_before = load_wcxb_source(args.dataset)
    manifest_bytes = args.sample_manifest.read_bytes()
    selected, _ = validate_manifest(records, source_before, json.loads(manifest_bytes))
    receipt, rows = load_full_paired_latency(args.evidence_root, profile,
                                             {row['track_id'] for row in selected})
    _, source_after = load_wcxb_source(args.dataset)
    if (source_before != source_after or source_before != receipt['source_sha256']
            or hashlib.sha256(manifest_bytes).hexdigest() != receipt['manifest_sha256']
            or hashlib.sha256(args.sample_manifest.read_bytes()).hexdigest() != receipt['manifest_sha256']
            or len(rows) != len(selected) * 2):
        raise ValueError('WCXB paired latency input or declared scope changed')
    certificate = {'schema_version': 1, 'kind': 'certified_wcxb_paired_latency',
                   'source_sha256_before': source_before, 'source_sha256_after': source_after,
                   'sample_sha256_before': receipt['manifest_sha256'],
                   'sample_sha256_after': receipt['manifest_sha256'],
                   'receipt_sha256': hashlib.sha256((args.evidence_root / 'receipt.json').read_bytes()).hexdigest(),
                   'calls_sha256': receipt['calls_sha256'],
                   'case_count': len(selected), 'physical_call_count': receipt['call_count'],
                   'selected_round': 1, 'complete': True}
    certificate_path.write_text(json.dumps(certificate, indent=2) + '\n')
    print('Certified WCXB paired latency cohort:', args.evidence_root)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'webmainbench-latency':
        from scripts.run_certified_webmainbench_latency import main as webmainbench_latency_main
        webmainbench_latency_main(sys.argv[2:])
    else:
        main()
