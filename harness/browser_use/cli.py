"""Command-line entrypoint for isolated browser use evaluation."""

from __future__ import annotations

import argparse

from .runner import run_evaluation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="/work/tasks.v2.json")
    parser.add_argument("--output", default="/work/output")
    parser.add_argument("--reports", default="/work/reports")
    parser.add_argument(
        "--ground-truth-evidence",
        default="/work/ground_truth_validation.json",
    )
    parser.add_argument("--failure-retries", type=int, default=1)
    parser.add_argument("--sample-interval-ms", type=int, default=50)
    parser.add_argument("--settle-ms", type=int, default=1500)
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--llm-diagnostics", action="store_true")
    parser.add_argument("--diagnosis-model", default="gpt-5.6-sol")
    parser.add_argument("--diagnosis-reasoning-effort", default="low")
    args = parser.parse_args()
    run_evaluation(
        suite_path=args.suite,
        output_dir=args.output,
        reports_dir=args.reports,
        ground_truth_evidence_path=args.ground_truth_evidence,
        max_retries=args.failure_retries,
        sample_interval_ms=args.sample_interval_ms,
        settle_ms=args.settle_ms,
        cpus=args.cpus,
        memory=args.memory,
        llm_diagnostics=args.llm_diagnostics,
        diagnosis_model=args.diagnosis_model,
        diagnosis_reasoning_effort=args.diagnosis_reasoning_effort,
    )


if __name__ == "__main__":
    main()
