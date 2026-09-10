#!/usr/bin/env python3
"""Run one immutable benchmark cohort with mandatory pre/post reference checks."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERED = {"javascript-spa", "live-025-api-reference"}


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
    parser.add_argument("--browser-binary")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)

    case_ids = [value.strip() for value in args.case_ids.split(",") if value.strip()]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        parser.error("--case-ids must contain nonempty unique IDs")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    known = {case["id"] for case in dataset["cases"]}
    if set(case_ids) - known:
        parser.error("Unknown case IDs: " + ", ".join(sorted(set(case_ids) - known)))
    rendered = [case_id for case_id in case_ids if case_id in RENDERED]
    if rendered and not args.browser_binary:
        parser.error("--browser-binary is required for rendered reference cases")
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
    browser_args: list[str] = []
    if rendered:
        run([python, "scripts/capture_headless_browser_observations.py",
             "--dataset", str(args.dataset), "--output", str(pre_browser),
             "--case-ids", ",".join(rendered), "--browser-binary", args.browser_binary])
        browser_args = ["--browser-observations", str(pre_browser)]
    run([python, "scripts/freeze_headless_references.py", "--captures", str(pre_captures),
         "--dataset", str(args.dataset), "--output", str(frozen),
         "--case-ids", selected, *browser_args])

    benchmark = [python, "scripts/run_benchmark.py", "--dataset", str(frozen / "dataset.json"),
                 "--output", str(args.output), "--providers", args.providers,
                 "--case-ids", selected, "--workers", str(args.workers), "--measure-only"]
    if args.env_file:
        benchmark.extend(["--env-file", str(args.env_file)])
    run(benchmark)

    post_browser_args: list[str] = []
    if rendered:
        run([python, "scripts/capture_headless_browser_observations.py",
             "--dataset", str(args.output / "dataset.json"), "--output", str(post_browser),
             "--case-ids", ",".join(rendered), "--browser-binary", args.browser_binary])
        post_browser_args = ["--browser-observations", str(post_browser)]
    run([python, "scripts/postcheck_headless.py", "--run-dir", str(args.output),
         "--captures", str(post_captures), *post_browser_args])
    observations = post_captures / "observations.json"
    if not observations.exists():
        observations.parent.mkdir(parents=True, exist_ok=True)
        observations.write_text("[]\n", encoding="utf-8")
    run([python, "scripts/finalize_benchmark_run.py", "--run-dir", str(args.output),
         "--observations", str(observations)])
    print("Certified benchmark cohort:", args.output)


if __name__ == "__main__":
    main()
