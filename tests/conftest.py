"""Optional historical source-input checks; portable tests do not require downloads."""

import importlib
import os
import sys
from pathlib import Path

import pytest


def guard_browser_launches(patch):
    """Fail before macOS application registration crashes a restricted browser."""
    if sys.platform != "darwin" or os.environ.get("CODEX_SANDBOX") != "seatbelt":
        return

    def unavailable(*args, **kwargs):
        raise RuntimeError(
            "macOS browser tests require an unrestricted process; "
            "rerun this test command outside the restricted execution environment"
        )

    for name in ("playwright.sync_api", "playwright.async_api"):
        try:
            api = importlib.import_module(name)
        except ImportError:
            continue
        for method in ("launch", "launch_persistent_context"):
            patch.setattr(api.BrowserType, method, unavailable)


@pytest.fixture(scope="session", autouse=True)
def browser_launch_policy():
    with pytest.MonkeyPatch.context() as patch:
        guard_browser_launches(patch)
        yield


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
        from lexbench_webfetch.inputs import definition_path

        try:
            for manifest in sorted(
                definition_path("evaluation").rglob("source-inputs.json")
            ):
                main(["--manifest", str(manifest), "--check"])
        except (FileNotFoundError, ValueError) as error:
            raise pytest.UsageError(str(error)) from error
