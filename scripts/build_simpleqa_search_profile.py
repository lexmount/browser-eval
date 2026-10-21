"""Rebuild and verify the frozen 831-question SimpleQA retrieval profile."""

import argparse
import json
from pathlib import Path

from harness.web_search.core import write_json
from harness.web_search.simpleqa import rebuild_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    profile = rebuild_profile(json.loads(args.template.read_text()), args.csv.read_bytes())
    write_json(args.output, profile)


if __name__ == "__main__":
    main()
