#!/usr/bin/env python3
"""Create the deterministic per-attempt result review."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.browser_use.review import write_review
from harness.browser_use.selection import load_pilot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("evaluation/browser-use/webarena-verified-pilot.json"),
    )
    args = parser.parse_args()
    selection = load_pilot(args.selection)
    print(write_review(evidence=args.evidence, selection_sha256=selection.sha256))


if __name__ == "__main__":
    main()
