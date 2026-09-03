"""Validate and add a complete provider run to canonical evidence."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.evidence import add_provider_run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--track", help="Add only a complete, previously unmeasured track for this provider")
    parser.add_argument(
        "--implementation", type=Path, help="Verified implementation version/source/hash JSON"
    )
    args = parser.parse_args()
    implementation = json.loads(args.implementation.read_text()) if args.implementation else None
    print(
        "Added",
        add_provider_run(ROOT / "evaluation", args.run_dir, args.provider, implementation, track=args.track),
        "records",
    )
