"""Build the current-evidence Chromium revalidation suite for eligible trajectories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.qualification import build_evidence_revalidation_suite
from harness.trajectory_replay.public_webarena.suite import load_suite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_evidence_revalidation_suite(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
