from __future__ import annotations

import importlib.machinery
import json
from pathlib import Path

import pytest

from harness.trajectory_replay import (
    catalog_acceptance,
    catalog_consolidation,
    catalog_release,
    catalog_runner,
)
from harness.trajectory_replay import webarena_runtime_source as runtime_source

REVISION = runtime_source.WEBARENA_VERIFIED_REVISION
FORK = runtime_source.WEBARENA_VERIFIED_FORK_URL


class _Distribution:
    def __init__(self, root: Path, direct_url: object, *, owns_package: bool = True):
        self.root = root
        self.direct_url = direct_url
        self.files = [Path("webarena_verified/__init__.py")] if owns_package else [Path("other.py")]

    def read_text(self, name: str):
        assert name == "direct_url.json"
        if isinstance(self.direct_url, str):
            return self.direct_url
        if self.direct_url is None:
            return None
        return json.dumps(self.direct_url)

    def locate_file(self, path):
        return self.root / path


def _direct_url(*, url=FORK, revision=REVISION, requested_revision=REVISION):
    return {
        "url": url,
        "vcs_info": {
            "vcs": "git",
            "commit_id": revision,
            "requested_revision": requested_revision,
        },
    }


def _install(monkeypatch, tmp_path, direct_url, *, module_root=None, owns_package=True):
    distribution = _Distribution(tmp_path, direct_url, owns_package=owns_package)
    expected_root = tmp_path / "webarena_verified"
    expected_root.mkdir(parents=True)
    (expected_root / "__init__.py").write_text("", encoding="utf-8")
    selected_root = module_root or expected_root
    spec = importlib.machinery.ModuleSpec(
        "webarena_verified",
        loader=None,
        origin=str(selected_root / "__init__.py"),
        is_package=True,
    )
    spec.submodule_search_locations = [str(selected_root)]
    monkeypatch.setattr(
        runtime_source.importlib.metadata, "distribution", lambda _name: distribution
    )
    monkeypatch.setattr(runtime_source.importlib.util, "find_spec", lambda _name: spec)
    return expected_root


def test_accepts_exact_lexmount_fork_revision_and_owned_module(monkeypatch, tmp_path):
    expected_root = _install(monkeypatch, tmp_path, _direct_url())
    identity = runtime_source.verify_webarena_runtime_source()
    assert identity.repository_url == FORK
    assert identity.revision == REVISION
    assert identity.module_root == expected_root.resolve()


def test_rejects_servicenow_repository(monkeypatch, tmp_path):
    _install(
        monkeypatch,
        tmp_path,
        _direct_url(url="https://github.com/ServiceNow/webarena-verified.git"),
    )
    with pytest.raises(RuntimeError, match="Lexmount fork"):
        runtime_source.verify_webarena_runtime_source()


@pytest.mark.parametrize("direct_url", [None, {"archive_info": {}}, {"url": "pypi"}])
def test_rejects_pypi_or_missing_direct_url(monkeypatch, tmp_path, direct_url):
    _install(monkeypatch, tmp_path, direct_url)
    with pytest.raises(RuntimeError, match="direct_url"):
        runtime_source.verify_webarena_runtime_source()


def test_rejects_wrong_commit(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _direct_url(revision="b" * 40))
    with pytest.raises(RuntimeError, match="revision"):
        runtime_source.verify_webarena_runtime_source()


@pytest.mark.parametrize(
    "direct_url",
    ["{", [], {"url": FORK, "vcs_info": "git"}, _direct_url(requested_revision=1)],
)
def test_rejects_malformed_metadata(monkeypatch, tmp_path, direct_url):
    _install(monkeypatch, tmp_path, direct_url)
    with pytest.raises(RuntimeError, match="metadata|revision"):
        runtime_source.verify_webarena_runtime_source()


def test_rejects_module_path_injection(monkeypatch, tmp_path):
    injected = tmp_path / "injected" / "webarena_verified"
    _install(monkeypatch, tmp_path / "distribution", _direct_url(), module_root=injected)
    with pytest.raises(RuntimeError, match="import path"):
        runtime_source.verify_webarena_runtime_source()


def test_rejects_distribution_without_package_ownership(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _direct_url(), owns_package=False)
    with pytest.raises(RuntimeError, match="does not own"):
        runtime_source.verify_webarena_runtime_source()


def test_acceptance_checks_source_before_reading_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        catalog_acceptance,
        "verify_webarena_runtime_source",
        lambda: (_ for _ in ()).throw(RuntimeError("wrong source")),
    )
    with pytest.raises(RuntimeError, match="wrong source"):
        catalog_acceptance.audit(tmp_path / "missing", [], [])


def test_consolidation_checks_source_before_reading_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        catalog_consolidation,
        "verify_webarena_runtime_source",
        lambda: (_ for _ in ()).throw(RuntimeError("wrong source")),
    )
    with pytest.raises(RuntimeError, match="wrong source"):
        catalog_consolidation.consolidate(tmp_path / "missing", tmp_path, [], tmp_path / "sites")


def test_runner_checks_source_before_browser_or_input_access(monkeypatch, tmp_path):
    monkeypatch.setattr(
        catalog_runner,
        "verify_webarena_runtime_source",
        lambda: (_ for _ in ()).throw(RuntimeError("wrong source")),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--catalog",
            str(tmp_path / "catalog"),
            "--portable-suite",
            str(tmp_path / "suite"),
            "--site-map",
            str(tmp_path / "sites"),
            "--site-reset-receipt",
            str(tmp_path / "receipt"),
            "--environment-profile",
            str(tmp_path / "environment"),
            "--binary",
            str(tmp_path / "browser"),
            "--output",
            str(tmp_path / "output"),
        ],
    )
    with pytest.raises(RuntimeError, match="wrong source"):
        catalog_runner.main()


def test_release_checks_source_before_profile_access(monkeypatch):
    monkeypatch.setattr(
        catalog_release,
        "verify_webarena_runtime_source",
        lambda: (_ for _ in ()).throw(RuntimeError("wrong source")),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "release-check",
            "--profile",
            "external-profile.json",
            "--comparison-release",
            "external-comparison.json",
        ],
    )
    with pytest.raises(RuntimeError, match="wrong source"):
        catalog_release.main()
