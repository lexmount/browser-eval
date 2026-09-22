"""Create a resumable full-split Chromium queue without repeating proven passes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_queue(
    scope_path: Path, sample_path: Path, passed_results_path: Path, human_suite_path: Path
) -> dict:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    passed = json.loads(passed_results_path.read_text(encoding="utf-8"))
    human = json.loads(human_suite_path.read_text(encoding="utf-8"))
    if scope.get("schema") != "lex_webarena_verified_full_scope/1":
        raise ValueError("full split scope is invalid")
    ids = scope.get("task_ids")
    if ids != list(range(scope.get("task_count", -1))):
        raise ValueError("full split no longer has official task order")
    if _sha256(sample_path) != scope["independent_sample_split"]["sha256"]:
        raise ValueError("sample split changed after full scope was frozen")
    sample_ids = {row["task_id"] for row in sample["tasks"]}
    if len(sample_ids) != scope["independent_sample_split"]["task_count"]:
        raise ValueError("sample split cardinality changed")
    if passed.get("status") != "complete" or set(passed.get("task_ids") or []) != sample_ids:
        raise ValueError("inherited result set does not cover the complete sample split")
    chromium = [row for row in passed.get("attempts") or [] if row.get("engine") == "chromium"]
    if len(chromium) != len(sample_ids) or {row.get("task_id") for row in chromium} != sample_ids:
        raise ValueError("inherited Chromium results are incomplete or duplicated")
    pass_bindings = {}
    for row in chromium:
        task_id = row["task_id"]
        if row.get("status") != "pass" or row.get("evaluator_status") != "pass":
            raise ValueError(f"task {task_id} is not a verified inherited pass")
        evidence = (row.get("evidence") or {}).get("evaluation") or {}
        path = Path(str(evidence.get("path") or ""))
        if not path.is_file() or path.is_symlink() or _sha256(path) != evidence.get("sha256"):
            raise ValueError(f"task {task_id} has no intact evaluator evidence")
        evaluation = json.loads(path.read_text(encoding="utf-8"))
        if evaluation.get("task_id") != task_id or float(evaluation.get("score") or 0) != 1:
            raise ValueError(f"task {task_id} evaluator evidence does not pass")
        pass_bindings[task_id] = evidence["sha256"]
    human_candidates = {}
    for task in human.get("tasks") or []:
        task_id = int(str(task["id"]).removeprefix("webarena-"))
        if task_id in human_candidates or task_id not in ids:
            raise ValueError(f"duplicate or unknown human recording: {task_id}")
        if (task.get("trajectory") or {}).get("actions"):
            human_candidates[task_id] = True
    rows = []
    for task_id in ids:
        if task_id in pass_bindings:
            rows.append(
                {
                    "task_id": task_id,
                    "status": "pass",
                    "source": "inherited_sample_chromium",
                    "evaluation_sha256": pass_bindings[task_id],
                }
            )
        elif task_id in human_candidates:
            rows.append(
                {
                    "task_id": task_id,
                    "status": "pending_first_sweep",
                    "source": "unqualified_official_human_recording",
                }
            )
        else:
            rows.append(
                {
                    "task_id": task_id,
                    "status": "needs_trajectory",
                    "source": None,
                }
            )
    return {
        "schema": "lex_webarena_verified_full_queue/1",
        "scope_sha256": _sha256(scope_path),
        "inherited_results_sha256": _sha256(passed_results_path),
        "human_suite_sha256": _sha256(human_suite_path),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified-full/scope.json"),
    )
    parser.add_argument(
        "--sample-selection",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified/selection.json"),
    )
    parser.add_argument("--passed-results", type=Path, required=True)
    parser.add_argument(
        "--human-suite",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    queue = build_queue(args.scope, args.sample_selection, args.passed_results, args.human_suite)
    if args.output.exists():
        raise ValueError("queue output already exists; use a new path for a new snapshot")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {
        status: sum(row["status"] == status for row in queue["rows"])
        for status in ("pass", "pending_first_sweep", "needs_trajectory")
    }
    print(json.dumps({"counts": counts, "sha256": _sha256(args.output)}))


if __name__ == "__main__":
    main()
