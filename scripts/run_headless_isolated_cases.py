#!/usr/bin/env python3
"""Run one provider in a fresh process per case and retain every logical result.

This is for providers whose timed call cannot be cancelled safely. A timeout
quarantines only that child process instead of preventing unrelated cases from
being measured.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--case-ids", required=True)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; isolated evidence must not be overwritten")
    cases = [case_id for case_id in args.case_ids.split(",") if case_id]
    if not cases or len(cases) != len(set(cases)):
        parser.error("Case IDs must be non-empty and unique")
    args.output.mkdir(parents=True)
    receipts = []
    for index, case_id in enumerate(cases, 1):
        destination = args.output / case_id
        command = [
            sys.executable, str(ROOT / "scripts/run_headless_benchmark.py"),
            "--output", str(destination), "--dataset", str(args.dataset),
            "--providers", args.provider, "--case-ids", case_id,
            "--workers", "1", "--timeout", str(args.timeout),
        ]
        if args.env_file:
            command.extend(["--env-file", str(args.env_file)])
        completed = subprocess.run(command, cwd=ROOT, env=os.environ.copy())
        result_path = destination / "results.jsonl"
        rows = result_path.read_text().splitlines() if result_path.is_file() else []
        if len(rows) != 1:
            raise RuntimeError(f"{case_id}: child retained {len(rows)} logical results")
        record = json.loads(rows[0])
        if record.get("provider") != args.provider or record.get("case_id") != case_id:
            raise RuntimeError(f"{case_id}: child result identity mismatch")
        receipts.append({
            "case_id": case_id,
            "directory": case_id,
            "child_exit_code": completed.returncode,
            "attempt_id": record["attempt_id"],
        })
        print(f"Isolated {index}/{len(cases)}: {case_id} (exit {completed.returncode})", flush=True)
    (args.output / "isolated-runs.json").write_text(
        json.dumps({"provider": args.provider, "runs": receipts}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
