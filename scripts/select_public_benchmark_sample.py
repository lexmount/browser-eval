"""Freeze the selected WebMainBench-545 public benchmark subset."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.web_fetch.public_benchmark_sample import DEFAULT_SEED, build_manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/public-webmainbench-200.json")
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    args = parser.parse_args()
    manifest = build_manifest(args.source, args.output, args.size, args.seed)
    print(f"{args.output}: {len(manifest['cases'])} cases")
