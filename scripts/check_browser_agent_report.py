#!/usr/bin/env python3
"""Validate the autonomous browser-agent report and its measured evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.browser_agent.selection import load_pilot


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(
    report_dir: Path,
    evidence_dir: Path,
    selection_path: Path,
    *,
    require_complete: bool,
) -> None:
    index = report_dir / "index.html"
    review_page = report_dir / "review.html"
    reproduction_page = report_dir / "reproduction.html"
    audit_path = report_dir / "report-data.json"
    attempts_path = evidence_dir / "attempts.jsonl"
    manifest_path = evidence_dir / "manifest.json"
    run_config_path = evidence_dir / "run-config.json"
    if not all(path.is_file() for path in (index, review_page, reproduction_page, audit_path)):
        raise ValueError(
            "report must contain index.html, review.html, reproduction.html, and report-data.json"
        )
    selection = load_pilot(selection_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("selection_sha256") != selection.sha256:
        raise ValueError("report was not built from the current pre-registered selection")
    rows = []
    if attempts_path.exists():
        rows = [
            json.loads(line)
            for line in attempts_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    selected_ids = {task.task_id for task in selection.tasks}
    expected_primary = {
        (engine, task_id, 1)
        for engine in ("chromium", "moli")
        for task_id in selected_ids
    }
    by_key: dict[tuple[str, int, int], dict] = {}
    for row in rows:
        key = (str(row["engine"]), int(row["task_id"]), int(row["attempt"]))
        if key in by_key:
            raise ValueError(f"duplicate measured attempt: {key}")
        if key[0] not in {"chromium", "moli"} or key[1] not in selected_ids:
            raise ValueError(f"attempt outside the registered cohort: {key}")
        if key[2] not in {1, 2}:
            raise ValueError(f"more than one retry is not allowed: {key}")
        by_key[key] = row
    for engine, task_id, attempt in by_key:
        if attempt == 2:
            primary = by_key.get((engine, task_id, 1))
            if primary is None or float(primary["score"]) == 1.0:
                raise ValueError(
                    f"retry is allowed only after a failed primary: {(engine, task_id)}"
                )
    html = index.read_text(encoding="utf-8")
    if "expected_response" in html or "reference_answer" in html:
        raise ValueError("private evaluator answers must not be published")
    if require_complete:
        if not manifest_path.is_file() or not run_config_path.is_file():
            raise ValueError("complete evidence requires manifest.json and run-config.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_config = json.loads(run_config_path.read_text(encoding="utf-8"))
        if manifest.get("selection_sha256") != selection.sha256:
            raise ValueError("manifest selection hash differs from the registered cohort")
        if run_config.get("selection_sha256") != selection.sha256:
            raise ValueError("run configuration differs from the registered cohort")
        if run_config.get("reasoning_effort") != "low":
            raise ValueError("pilot reasoning effort must be low")
        if not run_config.get("browser_eval_commit"):
            raise ValueError("run configuration must identify the browser-eval commit")
        if manifest.get("protocol", {}).get("browser_eval_commit") != run_config.get(
            "browser_eval_commit"
        ):
            raise ValueError("manifest and run configuration disagree on browser-eval commit")
        if manifest.get("protocol", {}).get("model_transport_retries") != 1:
            raise ValueError("pilot allows exactly one model transport retry")
        missing = expected_primary - set(by_key)
        if missing:
            raise ValueError(f"missing {len(missing)} primary attempts")
        missing_retries = {
            (engine, task_id, 2)
            for engine, task_id, _ in expected_primary
            if float(by_key[(engine, task_id, 1)]["score"]) != 1.0
            and (engine, task_id, 2) not in by_key
        }
        if missing_retries:
            raise ValueError(f"missing {len(missing_retries)} permitted failure retries")
        if "测量尚未完整" in html:
            raise ValueError("complete publication cannot contain an incomplete-results notice")
        review_path = evidence_dir / "result-review.json"
        if not review_path.is_file():
            raise ValueError("complete publication requires result-review.json")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        bindings = review.get("bindings") or {}
        if bindings.get("attempts_sha256") != _sha256(attempts_path):
            raise ValueError("result review is not bound to the current attempts")
        if bindings.get("manifest_sha256") != _sha256(manifest_path):
            raise ValueError("result review is not bound to the current manifest")
        if bindings.get("selection_sha256") != selection.sha256:
            raise ValueError("result review is not bound to the registered selection")
        coverage = review.get("coverage") or {}
        if coverage != {"recorded_attempts": len(rows), "reviewed_attempts": len(rows)}:
            raise ValueError("result review does not cover every recorded attempt")
        if review.get("disagreements") or review.get("unresolved"):
            raise ValueError("result review contains disagreements or unresolved checks")
        reviewed_keys = {
            (str(item["engine"]), int(item["task_id"]), int(item["attempt"]))
            for item in review.get("attempts") or []
        }
        if reviewed_keys != set(by_key):
            raise ValueError("result review attempt inventory differs from measured evidence")
        report_review = audit.get("review") or {}
        if report_review.get("bindings") != bindings:
            raise ValueError("rendered report is not bound to the current result review")
        for page in (index, review_page, reproduction_page):
            text = page.read_text(encoding="utf-8")
            if str(evidence_dir.resolve()) in text:
                raise ValueError("published pages must not contain machine-local evidence paths")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("evaluation/browser-agent/webarena-verified-pilot.json"),
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    check(
        args.report,
        args.evidence,
        args.selection,
        require_complete=args.require_complete,
    )
    print("Browser-agent report integrity check passed")


if __name__ == "__main__":
    main()
