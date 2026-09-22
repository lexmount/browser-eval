"""Command-line interface for the pinned WebArena environment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from webarena_verified.environments.setup import setup_init
from webarena_verified.types.task import WebArenaSite

from .config import SITES, RuntimeConfig
from .manager import EnvironmentManager
from .map_archives import verify_archives


def _write_receipt(path: Path | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("setup", "verify", "start", "reset", "stop"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--site", choices=SITES, action="append")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--data-dir")
    parser.add_argument("--defer-removal", action="store_true")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    config = RuntimeConfig.load(args.config)
    names = tuple(args.site or config.sites)
    if args.command == "setup" and (
        not args.data_dir or any(name not in {"map", "wikipedia"} for name in names)
    ):
        parser.error("setup requires --site map|wikipedia and --data-dir")
    manager = (
        EnvironmentManager(config, defer_removal=args.defer_removal)
        if args.command != "setup"
        else None
    )
    rows: list[dict[str, Any]] = []
    for name in names:
        try:
            if args.command == "setup":
                archives = verify_archives(Path(args.data_dir)) if name == "map" else []
                result = setup_init(site=WebArenaSite(name), data_dir=Path(args.data_dir))
                rows.append({"site": name, "result": result, "archives": archives})
            elif args.command == "verify":
                rows.append(manager.verify(name))
            elif args.command == "stop":
                rows.append(manager.stop(name))
            else:
                rows.append(
                    manager.start(
                        name,
                        reset=args.command == "reset",
                        timeout=args.timeout,
                        data_dir=args.data_dir,
                    )
                )
        except Exception:
            _write_receipt(
                args.receipt,
                {
                    "schema": "lexmount_webarena_receipt/1",
                    "command": args.command,
                    "status": "partial_failure",
                    "failed_site": name,
                    "sites": rows,
                },
            )
            raise
    _write_receipt(
        args.receipt,
        {"schema": "lexmount_webarena_receipt/1", "command": args.command, "sites": rows},
    )


if __name__ == "__main__":
    main()
