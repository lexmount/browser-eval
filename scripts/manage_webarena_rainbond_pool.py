"""Create, reset, and destroy disposable Rainbond WebArena environment slices."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.rainbond_slices import (
    RainbondSlicePool,
    provision_pool,
    scale_pool,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision = subparsers.add_parser("provision")
    provision.add_argument("--pool-id", required=True)
    provision.add_argument("--count", type=int, required=True)
    provision.add_argument("--output", type=Path, required=True)
    scale = subparsers.add_parser("scale")
    scale.add_argument("--pool-id", required=True)
    scale.add_argument("--count", type=int, required=True)
    scale.add_argument("--minimum-ready", type=int, default=1)
    scale.add_argument("--output", type=Path, required=True)
    reset = subparsers.add_parser("reset")
    reset.add_argument("--slice-dir", type=Path, required=True)
    destroy = subparsers.add_parser("destroy")
    destroy.add_argument("--slice-dir", type=Path, action="append", required=True)
    args = parser.parse_args()

    if args.command == "provision":
        paths = provision_pool(
            args.config.resolve(), args.output.resolve(), args.pool_id, args.count
        )
        print(json.dumps([str(path / "webarena.config.json") for path in paths]))
        return

    if args.command == "scale":
        paths = scale_pool(
            args.config.resolve(),
            args.output.resolve(),
            args.pool_id,
            args.count,
            minimum_ready=args.minimum_ready,
        )
        print(json.dumps([str(path / "webarena.config.json") for path in paths]))
        return

    pool = RainbondSlicePool(args.config.resolve())
    if args.command == "reset":
        slot_dir = args.slice_dir.resolve()
        receipt = pool.reset(slot_dir / "slice-receipt.json")
        config = pool.runtime_config(receipt)
        target = slot_dir / "webarena.config.json"
        target.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(target)
        return

    for slot_dir in args.slice_dir:
        pool.destroy(slot_dir.resolve() / "slice-receipt.json")


if __name__ == "__main__":
    main()
