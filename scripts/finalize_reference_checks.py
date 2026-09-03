"""Finalize run-local body reference checks from independent offline JSON captures.

Observations are a list, or {"observations": [...]}. Each entry requires case_id,
independent: true, content_text, timezone-aware checked_at, scope_identity copied
exactly from manifest.reference_checks.cases[case_id], and completion containing
satisfied: true, elapsed_ms and timeout_ms. No network or browser work is done.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.reference_lifecycle import finalize_reference_checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    args = parser.parse_args(argv)
    result = finalize_reference_checks(args.run_dir, args.observations)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
