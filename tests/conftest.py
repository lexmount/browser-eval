"""Optional historical source-input checks; portable tests do not require downloads."""

from pathlib import Path

import pytest


def read_frozen_dataset_bytes(path):
    """Skip only an absent historical input; corrupt installed inputs must fail."""
    from harness.web_fetch.reference_inputs import read_dataset_bytes

    try:
        return read_dataset_bytes(path)
    except FileNotFoundError:
        pytest.skip("requires externally installed historical reference inputs: " + str(path))


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
            for manifest in sorted(
                (Path(__file__).resolve().parents[1] / "evaluation").rglob("source-inputs.json")
            ):
                main(["--manifest", str(manifest), "--check"])
        except (FileNotFoundError, ValueError) as error:
            raise pytest.UsageError(str(error)) from error
