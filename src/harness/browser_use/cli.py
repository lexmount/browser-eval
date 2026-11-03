"""Build, check, review, package and publish Browser Use reports."""
from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence

PROG = "python -m harness.browser_use"
COMMANDS = {
    "report": ("harness.browser_use.report", "main",
               "Build a report from measured evidence"),
    "check": ("harness.browser_use.report_check", "main",
              "Check a built report against its profile and evidence"),
    "visual": ("harness.browser_use.report_visual", "main",
               "Run the visual gate and write its receipt"),
    "review": ("harness.browser_use.review", "main",
               "Write the deterministic per-attempt result review"),
    "package": ("harness.browser_use.report_package", "main",
                "Package a checked report for its Artifact Site"),
    "publish": ("harness.browser_use.report_publish", "cli",
                "Publish a packaged report to its bound Artifact Site"),
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
    invoked = sys.argv
    # Owners read sys.argv: visual receipts hash report_visual.py, so its main cannot change.
    sys.argv = [f"{PROG} {args.command}", *remaining]
    try:
        handler()
    finally:
        sys.argv = invoked
