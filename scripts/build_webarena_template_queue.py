"""Schedule template proposals after the recorded-trajectory sweep completes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_template_queue(queue_path: Path, progress_path: Path, proposals_path: Path) -> dict:
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    progress = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    rows = queue.get("rows") or []
    ids = [row["task_id"] for row in rows]
    if (
        queue.get("schema") != "lex_webarena_verified_full_queue/1"
        or ids != list(range(len(ids)))
        or [row.get("task_id") for row in progress] != ids
        or proposals.get("schema") != "lex_webarena_template_candidates/1"
        or proposals.get("queue_sha256") != _sha256(queue_path)
    ):
        raise ValueError("template queue requires a complete ordered first sweep")
    candidates = {row["task_id"] for row in proposals["candidates"]}
    if len(candidates) != len(proposals["candidates"]):
        raise ValueError("template proposals contain duplicate task IDs")
    next_rows = []
    for original, result in zip(rows, progress, strict=True):
        task_id = original["task_id"]
        if result["status"] == "pass":
            next_rows.append(
                {"task_id": task_id, "status": "pass", "source": "first_sweep_or_inherited"}
            )
        elif original["status"] == "needs_trajectory" and task_id in candidates:
            next_rows.append(
                {"task_id": task_id, "status": "pending_first_sweep", "source": "template_transfer"}
            )
        elif original["status"] == "needs_trajectory":
            next_rows.append({"task_id": task_id, "status": "needs_trajectory", "source": None})
        else:
            next_rows.append(
                {
                    "task_id": task_id,
                    "status": "first_sweep_failed",
                    "source": "recorded_trajectory",
                }
            )
    return {
        "schema": "lex_webarena_verified_full_queue/1",
        "preceding_queue_sha256": _sha256(queue_path),
        "preceding_progress_sha256": _sha256(progress_path),
        "human_suite_sha256": queue["human_suite_sha256"],
        "template_candidates_sha256": _sha256(proposals_path),
        "rows": next_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_template_queue(args.queue, args.progress, args.proposals)
    if args.output.exists():
        raise ValueError("template queue output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    counts = {
        status: sum(row["status"] == status for row in result["rows"])
        for status in {row["status"] for row in result["rows"]}
    }
    print(json.dumps({"counts": counts, "sha256": _sha256(args.output)}))


if __name__ == "__main__":
    main()
