"""Apply fully adjudicated WebArena corrections to the canonical suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.qualification import apply_adjudication


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument(
        "--task-id",
        action="append",
        dest="task_ids",
        help="apply only these independently validated decisions and preserve all others",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    qualification = json.loads(args.qualification.read_text(encoding="utf-8"))
    result = apply_adjudication(
        suite,
        qualification,
        task_ids=set(args.task_ids) if args.task_ids else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
