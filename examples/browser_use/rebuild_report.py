"""Recalculate summary.json and reports from raw attempts without running an agent."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.browser_use.diagnostics import diagnose_summary
from harness.browser_use.metrics import summarize_attempts
from harness.browser_use.report import write_latest_report
from harness.browser_use.suite import load_suite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="artifacts/browser_use/latest")
    parser.add_argument("--reports", default="docs/reports")
    parser.add_argument("--suite", default="examples/browser_use/tasks.v4.json")
    parser.add_argument(
        "--ground-truth-evidence",
        default="examples/browser_use/ground_truth_validation.json",
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
    evidence = json.loads(Path(args.ground_truth_evidence).read_text(encoding="utf-8"))
    suite_sha256 = hashlib.sha256(suite_path.read_bytes()).hexdigest()
    if evidence.get("dataset_sha256") != suite_sha256:
        raise SystemExit("ground-truth evidence does not match the current dataset sha256")

    expected_by_task = {task.task_id: task.ground_truth["expected_outputs"] for task in suite.tasks}
    observed_by_task = {row["task_id"]: row for row in evidence.get("results") or []}
    if set(observed_by_task) != set(expected_by_task):
        raise SystemExit("ground-truth evidence task set does not match the suite")
    for task_id, expected in expected_by_task.items():
        row = observed_by_task[task_id]
        if row.get("status") != "pass" or row.get("observed") != expected:
            raise SystemExit(f"ground-truth evidence mismatch: {task_id}")

    summary = summarize_attempts(rows, engines=("chromium", "moli"))
    diagnose_summary(summary, use_llm=False)
    manifest["suite_sha256"] = suite_sha256
    manifest["suite"] = [
        {
            "task_id": task.task_id,
            "title": task.title,
            "step_count": task.step_count,
            "complexity": task.complexity,
            "tags": list(task.tags),
            "tool_calls": list(task.tool_calls),
            "assertions": list(task.assertions),
            "ground_truth": task.ground_truth,
            "source": task.source,
        }
        for task in suite.tasks
    ]
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
    summary_bytes = (
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    (artifact_dir / "summary.json").write_bytes(summary_bytes)
    manifest["artifact_provenance"] = {
        "artifact_directory": str(artifact_dir),
        "attempts_sha256": hashlib.sha256(attempts_bytes).hexdigest(),
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
    }
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_latest_report(args.reports, manifest=manifest, summary=summary)
    print(f"Recalculated {len(rows)} attempts; report: {args.reports}/browser-use-evaluation-latest.html")


if __name__ == "__main__":
    main()
