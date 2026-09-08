#!/usr/bin/env python3
"""Rebind scoring/reference receipts after an audited verdict-neutral cleanup."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    metadata_path = args.evidence / "measurement-metadata-revisions.json"
    metadata = json.loads(metadata_path.read_text())[-1]
    current = hashlib.sha256((args.evidence / "results.jsonl").read_bytes()).hexdigest()
    if (metadata.get("kind") != "legacy_single_call_retry_envelope_removal"
            or metadata.get("provider_calls") != 0
            or metadata.get("output_results_sha256") != current):
        raise ValueError("Current results are not bound to the metadata cleanup")
    receipt_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
    rebound = 0
    for name in ("scoring-revisions.json", "reference-revisions.json"):
        path = args.evidence / name
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        for row in rows:
            if row.get("output_results_sha256") != metadata["input_results_sha256"]:
                continue
            row["prior_output_results_sha256"] = row["output_results_sha256"]
            row["output_results_sha256"] = current
            row["rebound_after_metadata_cleanup"] = {
                "receipt_sha256": receipt_hash,
                "provider_calls": 0,
                "verdict_neutral": True,
            }
            rebound += 1
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    if not rebound:
        raise ValueError("No bound revision receipt matched the pre-cleanup results")
    print("Rebound revision receipts:", rebound)


if __name__ == "__main__":
    main()
