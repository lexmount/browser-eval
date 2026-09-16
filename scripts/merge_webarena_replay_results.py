#!/usr/bin/env python3
"""Overlay formal WebArena replay correction shards onto one current matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.webarena_results import build_effective_results

CURRENT_PROFILE = "evaluation/trajectory-replay/webarena-verified/current-run.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--qualified-dataset", required=True)
    parser.add_argument("--base-receipt", required=True)
    parser.add_argument("--correction-receipt", action="append", default=[])
    parser.add_argument("--extension-receipt", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_effective_results(
        profile_path=CURRENT_PROFILE,
        seed_manifest_path=args.seeds,
        qualified_dataset_path=args.qualified_dataset,
        base_receipt_path=args.base_receipt,
        correction_receipt_paths=args.correction_receipt,
        extension_receipt_paths=args.extension_receipt,
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
