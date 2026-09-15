#!/usr/bin/env python3
"""Merge reviewed expert repair candidates into a private replay seed manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.expert_seeds import build_expert_seed_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        "--base-seeds",
        dest="base",
        required=True,
        help="Existing Browser Use replay seed manifest",
    )
    parser.add_argument(
        "--repairs",
        "--repair-spec",
        dest="repairs",
        required=True,
        help="Explicit expert repair specification",
    )
    parser.add_argument("--output", required=True, help="Destination merged seed manifest")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_expert_seed_manifest(args.base, args.repairs)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "expert_repair_candidates": manifest["counts"]["expert_repair_candidates"],
                "replay_ready_tasks": manifest["counts"]["replay_ready_tasks"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
