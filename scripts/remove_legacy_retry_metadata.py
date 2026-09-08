#!/usr/bin/env python3
"""Remove legacy retry envelopes only when exactly one physical call occurred."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    path = args.evidence / "results.jsonl"
    before = digest(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    changed = []
    for row in rows:
        metadata = row.get("provider_metadata", {})
        retry = metadata.get("retry")
        if not retry:
            continue
        attempts = retry.get("attempts")
        if (retry.get("physical_attempts") != 1 or retry.get("retries_used") != 0
                or not isinstance(attempts, list) or len(attempts) != 1
                or attempts[0].get("physical_attempt") != 1
                or attempts[0].get("content_sha256") != row["metrics"]["content_sha256"]):
            raise ValueError("Not a single-call legacy retry envelope: " + row["attempt_id"])
        del metadata["retry"]
        changed.append(row["attempt_id"])
    temporary = args.evidence / "results.jsonl.tmp"
    temporary.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows))
    temporary.replace(path)
    receipt = {
        "kind": "legacy_single_call_retry_envelope_removal",
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "input_results_sha256": before,
        "output_results_sha256": digest(path),
        "changed_attempts": changed,
        "provider_calls": 0,
        "preserved": "attempt identity, output bytes/hash, status, timing and metrics",
    }
    (args.evidence / "measurement-metadata-revisions.json").write_text(
        json.dumps([receipt], ensure_ascii=False, indent=2) + "\n")
    print("Removed legacy single-call retry envelopes:", len(changed))


if __name__ == "__main__":
    main()
