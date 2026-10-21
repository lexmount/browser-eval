"""Build the fixed SimpleQA Verified Search-API sample profile."""

import argparse
import json
from pathlib import Path

from harness.web_search.core import write_json
from harness.web_search.simpleqa import sampled_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20261021)
    args = parser.parse_args(argv)
    profile = sampled_profile(
        json.loads(args.template.read_text()),
        args.csv.read_bytes(),
        size=args.size,
        seed=args.seed,
    )
    write_json(args.output, profile)


if __name__ == "__main__":
    main()
