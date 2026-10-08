"""Optional historical source-input checks; portable tests do not require downloads."""

from pathlib import Path

import pytest


@pytest.fixture
def historical_dataset():
    from harness.web_fetch.config import load_dataset

    try:
        return load_dataset(Path(__file__).resolve().parents[1] / "evaluation/dataset.json")
    except FileNotFoundError:
        pytest.skip("requires externally installed historical reference inputs")


def pytest_addoption(parser):
    parser.addoption(
        "--require-reference-inputs",
        action="store_true",
        help="Fail unless the complete hash-bound historical source inputs are installed",
    )


def pytest_sessionstart(session):
    if session.config.getoption("--require-reference-inputs"):
        from harness.web_fetch.reference_inputs import main

        try:
            main(
                [
                    "--manifest",
                    str(Path(__file__).resolve().parents[1] / "evaluation/source-inputs.json"),
                    "--check",
                ]
            )
        except (FileNotFoundError, ValueError) as error:
            raise pytest.UsageError(str(error)) from error
