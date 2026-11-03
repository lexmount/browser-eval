"""Optional historical source-input checks; portable tests do not require downloads."""

import importlib
import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

# Hermetic values: local credentials must not reach tests, and nothing here is real.
def _recording_gateway():
    """Read the gateway from a pinned input that still names it instead of restating it."""
    pinned = Path(__file__).resolve().parents[1] / (
        "evaluation/trajectory-replay/webarena-verified/trajectory-candidates/task-771.json"
    )
    found = re.search(r"http://((?:\d{1,3}\.){3}\d{1,3}):", pinned.read_text(encoding="utf-8"))
    return found.group(1) if found else "192.0.2.5"


os.environ["WEBARENA_RECORDING_GATEWAYS"] = _recording_gateway()
for _site in ("SHOPPING", "SHOPPING_ADMIN", "REDDIT", "GITLAB"):
    os.environ[f"WEBARENA_{_site}_USERNAME"] = f"synthetic-{_site.lower()}-user"
    os.environ[f"WEBARENA_{_site}_PASSWORD"] = f"synthetic-{_site.lower()}-secret"


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


def _private_report_check():
    """Execute a private copy of the release checker against the current registry."""
    from harness.browser_use import report_check

    spec = importlib.util.spec_from_file_location(
        "report_check_registry_probe", report_check.__file__
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def report_check_with_site_password(monkeypatch):
    """Load a private copy of the release checker against a registry holding one more password."""

    def load(password):
        from harness.environments.webarena import topology

        resolved = topology.known_site_secrets()
        monkeypatch.setattr(topology, "known_site_secrets", lambda: (*resolved, password))
        return _private_report_check()

    return load


@pytest.fixture
def report_check_without_site_passwords(monkeypatch):
    """Load a private copy of the release checker against a registry holding no passwords."""
    from harness.environments.webarena import topology

    monkeypatch.setattr(topology, "known_site_secrets", tuple)
    return _private_report_check()


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
