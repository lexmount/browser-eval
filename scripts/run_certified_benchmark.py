#!/usr/bin/env python3
"""Run one immutable benchmark cohort with mandatory pre/post reference checks."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    parser.add_argument("--reference-promotion", type=Path)
    args = parser.parse_args(argv)
    if args.browser_capture_attempts < 1:
        parser.error("--browser-capture-attempts must be at least 1")

    case_ids = [value.strip() for value in args.case_ids.split(",") if value.strip()]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        parser.error("--case-ids must contain nonempty unique IDs")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    known = {case["id"] for case in dataset["cases"]}
    if set(case_ids) - known:
        parser.error("Unknown case IDs: " + ", ".join(sorted(set(case_ids) - known)))
    if args.output.exists() or args.work_root.exists():
        parser.error("Output and work root must both be new immutable paths")

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
    print("Certified benchmark cohort:", args.output)


if __name__ == "__main__":
    main()
