#!/usr/bin/env python3
"""Build private fixed-trajectory candidates from measured Browser Use evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.browser_use_seeds import build_replay_seed_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    manifest = build_replay_seed_manifest(args.evidence, profile)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **manifest["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
