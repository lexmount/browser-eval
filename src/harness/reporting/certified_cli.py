"""Dispatch certified collection through each track's maintained owner."""

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['web-search']:
        if any(value == '--track' or value.startswith('--track=') for value in argv):
            raise ValueError('Select the certified track once')
        argv = ['--track', 'web-search', *argv[1:]]
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--track", choices=["webfetch", "web-extract", "web-search"], default="webfetch")
    args, remaining = parser.parse_known_args(argv)
    if args.track == 'web-search':
        from harness.web_search.runner import main as run

        return run(remaining, certified=True)
    if args.track == "web-extract":
        from harness.web_extract.certification import main as run
    else:
        from harness.web_fetch.certification import main as run
    return run(remaining)
