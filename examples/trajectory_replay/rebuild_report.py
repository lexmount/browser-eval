"""Recalculate summary.json and reports from raw attempts without running an agent."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.diagnostics import diagnose_summary
from harness.trajectory_replay.metrics import summarize_attempts
from harness.trajectory_replay.report import write_latest_report
from harness.trajectory_replay.suite import load_suite, validate_ground_truth_evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--reports", help="Defaults to the selected run's report directory")
    parser.add_argument("--suite", default="examples/trajectory_replay/tasks.v4.json")
    parser.add_argument(
        "--ground-truth-evidence",
        default="examples/trajectory_replay/ground_truth_validation.json",
    )
    args = parser.parse_args()

    artifact_dir = Path(args.artifacts)
    suite_path = Path(args.suite)
    rows = [
        json.loads(line)
        for line in (artifact_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    suite = load_suite(suite_path)
    suite_sha256 = hashlib.sha256(suite_path.read_bytes()).hexdigest()
    if manifest.get("suite_sha256") != suite_sha256:
        raise SystemExit("artifact manifest suite sha256 does not match the requested suite")
    manifest_task_ids = {str(task.get("task_id")) for task in manifest.get("suite") or []}
    suite_task_ids = {task.task_id for task in suite.tasks}
    attempt_task_ids = {str(row.get("task_id")) for row in rows}
    if manifest_task_ids != suite_task_ids or attempt_task_ids != suite_task_ids:
        raise SystemExit("artifact task set does not match the requested suite")
    try:
        evidence = validate_ground_truth_evidence(suite_path, suite, args.ground_truth_evidence)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    summary = summarize_attempts(rows, engines=("chromium", "moli"))
    diagnose_summary(summary, use_llm=False)
    manifest["ground_truth_validation"] = {
        key: evidence.get(key)
        for key in (
            "schema",
            "dataset",
            "dataset_sha256",
            "source_commit",
            "validator",
            "method",
            "started_at",
            "finished_at",
            "automatic_browser_replay",
            "human_review",
        )
    }
    attempts_bytes = (artifact_dir / "attempts.jsonl").read_bytes()
    summary_bytes = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (artifact_dir / "summary.json").write_bytes(summary_bytes)
    manifest["artifact_provenance"] = {
        "artifact_directory": str(artifact_dir),
        "attempts_sha256": hashlib.sha256(attempts_bytes).hexdigest(),
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
    }
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    reports = Path(args.reports) if args.reports else artifact_dir / "report"
    write_latest_report(reports, manifest=manifest, summary=summary)
    report_path = reports / "trajectory-replay-evaluation-latest.html"
    print(f"Recalculated {len(rows)} attempts; report: {report_path}")


if __name__ == "__main__":
    main()
