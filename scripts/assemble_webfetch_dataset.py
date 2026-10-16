#!/usr/bin/env python3
"""Compile a registered Web Fetch dataset without making provider calls."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.web_fetch.dataset_assembly import assemble_profile_dataset


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument(
        "--profile",
        type=Path,
        help="external report profile; required when the reusable assembly does not bind one",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("--output must be a new path")
    _dataset, raw = assemble_profile_dataset(
        args.assembly, profile_path_override=args.profile
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)
    print(args.output)


if __name__ == "__main__":
    main()
