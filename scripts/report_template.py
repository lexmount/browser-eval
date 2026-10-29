"""Locate the existing renderer, example and release commands for a report type."""

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.reporting.templates import resolve_template

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "track",
        choices=tuple(
            json.loads(
                (Path(__file__).resolve().parents[1] / "configs/report-templates.json").read_text()
            )["tracks"]
        ),
    )
    args = parser.parse_args()
    print(json.dumps(resolve_template(args.track), ensure_ascii=False, indent=2))
