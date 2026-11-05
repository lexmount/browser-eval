"""Dispatch certified collection through each track's maintained owner."""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--track", choices=["webfetch", "web-extract"], default="webfetch")
    args, remaining = parser.parse_known_args(argv)
    if args.track == "web-extract":
        from harness.web_extract.certification import main as run
    else:
        from harness.web_fetch.certification import main as run
    return run(remaining)
