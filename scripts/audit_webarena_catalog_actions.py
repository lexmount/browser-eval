#!/usr/bin/env python3
"""Audit evaluate syntax and verify the catalog's action override data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.catalog_action_audit import (
    scan_function_body_actions,
    validate_function_body_overrides,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--action-overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    audit = scan_function_body_actions(args.catalog, args.portable_suite)
    validate_function_body_overrides(audit, args.action_overrides)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "evaluate_actions": audit["evaluate_action_count"],
                "function_body_findings": len(audit["function_body_findings"]),
            }
        )
    )


if __name__ == "__main__":
    main()
