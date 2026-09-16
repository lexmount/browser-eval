"""Create, reset, and destroy disposable Rainbond WebArena environment slices."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.rainbond_slices import (
    RainbondSlicePool,
    acquire_slice,
    provision_pool,
    reconcile_pool_capacity,
    release_slice,
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
    scale_group = scale.add_mutually_exclusive_group(required=True)
    scale_group.add_argument("--count", type=int)
    scale_group.add_argument("--active", type=int)
    scale.add_argument("--minimum-ready", type=int, default=1)
    scale.add_argument("--output", type=Path, required=True)
    reset = subparsers.add_parser("reset")
    reset.add_argument("--slice-dir", type=Path, required=True)
    destroy = subparsers.add_parser("destroy")
    destroy.add_argument("--slice-dir", type=Path, action="append", required=True)
    acquire = subparsers.add_parser("acquire")
    acquire.add_argument("--output", type=Path, required=True)
    acquire.add_argument("--owner", required=True)
    release = subparsers.add_parser("release")
    release.add_argument("--output", type=Path, required=True)
    release.add_argument("--lease-id", required=True)
    args = parser.parse_args()

    if args.command == "provision":
        paths = provision_pool(
            args.config.resolve(), args.output.resolve(), args.pool_id, args.count
        )
        print(json.dumps([str(path / "webarena.config.json") for path in paths]))
        return

    if args.command == "scale":
        if args.active is not None:
            paths = reconcile_pool_capacity(
                args.config.resolve(),
                args.output.resolve(),
                args.pool_id,
                args.active,
                minimum_ready=args.minimum_ready,
            )
        else:
            paths = scale_pool(
                args.config.resolve(),
                args.output.resolve(),
                args.pool_id,
                args.count,
                minimum_ready=args.minimum_ready,
            )
        print(json.dumps([str(path / "webarena.config.json") for path in paths]))
        return

    if args.command == "acquire":
        print(json.dumps(acquire_slice(args.output.resolve(), args.owner), sort_keys=True))
        return

    if args.command == "release":
        print(
            json.dumps(
                release_slice(args.config.resolve(), args.output.resolve(), args.lease_id),
                sort_keys=True,
            )
        )
        return

    pool = RainbondSlicePool(args.config.resolve())
    if args.command == "reset":
        slot_dir = args.slice_dir.resolve()
        receipt = pool.reset(slot_dir / "slice-receipt.json")
        target = slot_dir / "webarena.config.json"
        pool.persist_runtime_config(receipt, target)
        print(target)
        return

    first_error: BaseException | None = None
    for slot_dir in args.slice_dir:
        try:
            pool.destroy(slot_dir.resolve() / "slice-receipt.json")
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            else:
                first_error.add_note(f"additional destroy failure for {slot_dir}: {exc}")
    if first_error is not None:
        raise first_error


if __name__ == "__main__":
    main()
