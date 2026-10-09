#!/usr/bin/env python3
"""Finalize every one-case run using one shared independent observation set."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.reference_lifecycle import build_observations, finalize_reference_checks
from harness.web_fetch.safety import contained_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--observations", type=Path)
    source.add_argument("--captures", type=Path)
    args = parser.parse_args()
    receipt = json.loads((args.runs / "isolated-runs.json").read_text())
    if args.observations:
        payload = json.loads(args.observations.read_text())
        rows = (payload.get("observations") if "observations" in payload
                else list(payload.values())) if isinstance(payload, dict) else payload
        by_id = {row["case_id"]: row for row in rows}
        if len(by_id) != len(rows):
            raise ValueError("Duplicate observations")
    for item in receipt["runs"]:
        case_id = item["case_id"]
        root = contained_path(args.runs, item["directory"])
        if args.captures:
            rows = build_observations(root, args.captures)
            by_id = {row["case_id"]: row for row in rows}
        if case_id not in by_id:
            raise ValueError("Missing observation: " + case_id)
        filtered = contained_path(args.runs, "postchecks/" + case_id + ".json")
        filtered.parent.mkdir(parents=True, exist_ok=True)
        filtered.write_text(json.dumps([by_id[case_id]], ensure_ascii=False, indent=2) + "\n")
        result = finalize_reference_checks(root, filtered)
        if result["status"] != "verified":
            raise ValueError(f"{case_id}: {result['status']}")
        print(case_id, "verified", flush=True)


if __name__ == "__main__":
    main()
