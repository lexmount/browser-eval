"""Build, check, review, judge and publish Web Search reports."""
from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence

PROG = "python -m harness.web_search"
COMMANDS = {
    "report": ("harness.web_search.report", "main",
               "Build, check, release-check or export a report"),
    "visual": ("harness.web_search.visual", "main",
               "Capture the reader evidence that a release check binds"),
    "judge": ("harness.web_search.relevance", "judge_main",
              "Run explicitly selected model relevance judgments on a frozen report"),
    "review": ("harness.web_search.review", "main",
               "Freeze, run and collect the independent result review"),
    "publish": ("harness.web_search.publication", "main",
                "Freeze and publish a checked report to its bound Artifact Site"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, (_, _, summary) in COMMANDS.items():
        # The owning module parses the remaining arguments, including its own --help.
        sub.add_parser(name, help=summary, add_help=False)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args, remaining = build_parser().parse_known_args(argv)
    module, function, _ = COMMANDS[args.command]
    # Import on selection so each command and top-level help load only what they use.
    handler = getattr(importlib.import_module(module), function)
    invoked_as = sys.argv[0]
    # The owner's parser takes its usage name from argv[0].
    sys.argv[0] = f"{PROG} {args.command}"
    try:
        handler(remaining)
    finally:
        sys.argv[0] = invoked_as
