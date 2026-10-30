"""Browser tests reject the known macOS restriction before spawning Chrome."""
from types import SimpleNamespace

import pytest

from tests import conftest


@pytest.mark.parametrize("platform,restricted", [("darwin", True), ("darwin", False), ("linux", True)])
@pytest.mark.parametrize("method", ["launch", "launch_persistent_context"])
def test_browser_launch_environment_boundary(monkeypatch, platform, restricted, method):
    calls = []

    def launch(*args, **kwargs):
        calls.append(kwargs)
        return "browser-started"

    sync = SimpleNamespace(BrowserType=SimpleNamespace(launch=launch, launch_persistent_context=launch))
    asynchronous = SimpleNamespace(BrowserType=SimpleNamespace(launch=launch, launch_persistent_context=launch))
    monkeypatch.setattr(conftest.sys, "platform", platform)
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt" if restricted else "")
    monkeypatch.setattr(conftest.importlib, "import_module", lambda name: {
        "playwright.sync_api": sync, "playwright.async_api": asynchronous,
    }[name])
    conftest.guard_browser_launches(monkeypatch)
    for api in (sync, asynchronous):
        if platform == "darwin" and restricted:
            with pytest.raises(RuntimeError, match="unrestricted process"):
                getattr(api.BrowserType, method)(headless=True)
        else:
            assert getattr(api.BrowserType, method)(headless=True) == "browser-started"
    assert len(calls) == (0 if platform == "darwin" and restricted else 2)
