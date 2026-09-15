"""Build the exact corrected pending suite used for mandatory Chromium revalidation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.qualification import build_repair_validation_suite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    qualification = json.loads(args.qualification.read_text(encoding="utf-8"))
    result = build_repair_validation_suite(suite, qualification)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
