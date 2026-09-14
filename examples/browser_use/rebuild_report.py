"""Recalculate summary.json and reports from raw attempts without running an agent."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.browser_use.diagnostics import diagnose_summary
from harness.browser_use.metrics import summarize_attempts
from harness.browser_use.report import REPORT_STEM, write_latest_report
from harness.browser_use.suite import load_suite, validate_ground_truth_evidence


def validate_task_binding(
    *, suite_task_ids: set[str], manifest_task_ids: set[str], attempt_task_ids: set[str]
) -> None:
    if not manifest_task_ids or not manifest_task_ids.issubset(suite_task_ids):
        raise ValueError("artifact manifest task set is not contained in the requested suite")
    if attempt_task_ids != manifest_task_ids:
        raise ValueError("artifact attempt task set does not match the manifest")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", default="artifacts/browser_use/latest")
    parser.add_argument("--reports", default="docs/reports")
    parser.add_argument("--suite", default="examples/browser_use/tasks.v4.json")
    parser.add_argument(
        "--ground-truth-evidence",
        default=None,
        help=(
            "reviewed exact-output evidence; required only for suites that used it "
            "during execution"
        ),
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
    manifest_tasks = manifest.get("suite") or []
    manifest_task_ids = {str(task.get("task_id")) for task in manifest_tasks}
    suite_task_ids = {task.task_id for task in suite.tasks}
    attempt_task_ids = {str(row.get("task_id")) for row in rows}
    try:
        validate_task_binding(
            suite_task_ids=suite_task_ids,
            manifest_task_ids=manifest_task_ids,
            attempt_task_ids=attempt_task_ids,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    evidence = None
    if args.ground_truth_evidence:
        if manifest_task_ids != suite_task_ids:
            raise SystemExit(
                "exact-output ground-truth evidence cannot be rebound to a selected suite subset"
            )
        try:
            evidence = validate_ground_truth_evidence(
                suite_path, suite, args.ground_truth_evidence
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    engines = tuple(str(engine) for engine in (manifest.get("protocol") or {}).get("engines") or ())
    if not engines:
        engines = tuple(dict.fromkeys(str(row["engine"]) for row in rows))
    summary = summarize_attempts(rows, engines=engines)
    diagnose_summary(summary, use_llm=False)
    if evidence is not None:
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
    write_latest_report(args.reports, manifest=manifest, summary=summary)
    report_path = Path(args.reports) / f"{REPORT_STEM}.html"
    print(f"Recalculated {len(rows)} attempts; report: {report_path}")


if __name__ == "__main__":
    main()
