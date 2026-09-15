"""Turn a one-pass Chromium qualification run into a complete correction queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.qualification import build_repair_queue
from harness.trajectory_replay.public_webarena.suite import load_suite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--attempts", type=Path, required=True, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_repair_queue(load_suite(args.suite), args.attempts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
