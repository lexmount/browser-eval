"""Schedule original trajectories for every case still missing after template replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_authored_queue(
    template_queue_path: Path, template_progress_path: Path, authored_suite_path: Path
) -> dict:
    queue = json.loads(template_queue_path.read_text(encoding="utf-8"))
    progress = [
        json.loads(line)
        for line in template_progress_path.read_text(encoding="utf-8").splitlines()
    ]
    suite = load_suite(authored_suite_path)
    authored_ids = {int(task.task_id.removeprefix("webarena-")) for task in suite.tasks}
    rows = queue.get("rows") or []
    ids = [row["task_id"] for row in rows]
    if (
        queue.get("schema") != "lex_webarena_verified_full_queue/1"
        or ids != list(range(len(ids)))
        or [row.get("task_id") for row in progress] != ids
        or len(authored_ids) != len(suite.tasks)
    ):
        raise ValueError("authored queue requires a complete ordered template sweep")
    missing = {
        row["task_id"]
        for row, result in zip(rows, progress, strict=True)
        if row["status"] == "needs_trajectory" and result["status"] == "needs_trajectory"
    }
    if authored_ids != missing:
        raise ValueError("authored suite must cover every still-unattempted official case")
    next_rows = []
    for original, result in zip(rows, progress, strict=True):
        task_id = original["task_id"]
        if result["status"] == "pass":
            next_rows.append({"task_id": task_id, "status": "pass", "source": "earlier_pass"})
        elif task_id in authored_ids:
            next_rows.append(
                {
                    "task_id": task_id,
                    "status": "pending_first_sweep",
                    "source": "authored_trajectory",
                }
            )
        else:
            next_rows.append(
                {"task_id": task_id, "status": "first_sweep_failed", "source": "earlier_attempt"}
            )
    return {
        "schema": "lex_webarena_verified_full_queue/1",
        "preceding_queue_sha256": _sha256(template_queue_path),
        "preceding_progress_sha256": _sha256(template_progress_path),
        "human_suite_sha256": queue["human_suite_sha256"],
        "authored_suite_sha256": _sha256(authored_suite_path),
        "rows": next_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--authored-suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_authored_queue(args.queue, args.progress, args.authored_suite)
    if args.output.exists():
        raise ValueError("authored queue output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"scheduled": sum(r["status"] == "pending_first_sweep" for r in result["rows"])}
        )
    )


if __name__ == "__main__":
    main()
