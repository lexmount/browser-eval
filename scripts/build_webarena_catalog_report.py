#!/usr/bin/env python3
"""Build the full WebArena catalog report from accepted evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.webarena_report import build_catalog_acceptance_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    result = build_catalog_acceptance_report(
        evidence_root=args.evidence_root,
        ledger_path=args.ledger,
        scope_path=args.scope,
        catalog_path=args.catalog,
        suite_path=args.portable_suite,
        site_map_path=args.site_map,
        output=args.output,
        archive=args.archive,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
