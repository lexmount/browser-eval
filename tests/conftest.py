"""Optional historical source-input checks; portable tests do not require downloads."""

from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--require-reference-inputs",
        action="store_true",
        help="Fail unless the complete hash-bound historical source inputs are installed",
    )


def pytest_sessionstart(session):
    if session.config.getoption("--require-reference-inputs"):
        from harness.web_fetch.reference_inputs import input_tree

        try:
            input_tree(
                Path(__file__).resolve().parents[1] / "evaluation/dataset.json",
                "sources",
            )
        except (FileNotFoundError, ValueError) as error:
            raise pytest.UsageError(str(error)) from error
