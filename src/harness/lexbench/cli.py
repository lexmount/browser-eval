"""Build, check, package and reproduce the Lexbench comparison report."""
from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence

PROG = "python -m harness.lexbench"
COMMANDS = {
    "report": ("harness.lexbench.report", "Build a report from retained evidence"),
    "check": ("harness.lexbench.verification", "Validate a built report"),
    "package": ("harness.lexbench.pack", "Package a validated report"),
    "reproduce": ("harness.lexbench.reproduction", "Reproduce one frozen fixed-layout run"),
    "resource-profiles": ("harness.lexbench.resource_profiles",
                          "Execute the frozen Linux resource profile cohort"),
    "resource-matrix": ("harness.lexbench.resource_matrix",
                        "Execute the frozen resource matrix"),
    "resource-summary": ("harness.lexbench.resource_summary",
                         "Summarize completed Linux resource profiles"),
    "chart": ("harness.lexbench.charts", "Render one resource comparison chart"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, (_, summary) in COMMANDS.items():
        # The owning module parses the remaining arguments, including its own --help.
        sub.add_parser(name, help=summary, add_help=False)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args, remaining = build_parser().parse_known_args(argv)
    # Import on selection so each command and top-level help load only what they use.
    handler = importlib.import_module(COMMANDS[args.command][0]).main
    invoked = sys.argv
    # Owners take their usage line from sys.argv. The resource protocol and the public
    # bundle freeze the bytes of some of them, so they cannot name the command themselves.
    sys.argv = [f"{PROG} {args.command}", *remaining]
    try:
        handler()
    finally:
        sys.argv = invoked
