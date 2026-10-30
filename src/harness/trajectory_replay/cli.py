"""Command-line entrypoint for isolated browser trajectory replay."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.trajectory_replay.runner import run_evaluation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite", help="Explicit suite path; defaults to the locked HF curated edition"
    )
    parser.add_argument("--output", required=True, help="New immutable evidence directory")
    parser.add_argument("--reports", help="Defaults to the selected run's report directory")
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Save scores and evidence without building local HTML",
    )
    parser.add_argument(
        "--ground-truth-evidence",
        required=True,
    )
    parser.add_argument(
        "--agent-smoke", help="Explicit historical smoke evidence JSON; never rerun"
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
        reports_dir=Path(args.reports) if args.reports else Path(args.output) / "report",
        generate_report=not args.no_report,
        ground_truth_evidence_path=args.ground_truth_evidence,
        agent_smoke_path=args.agent_smoke,
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
