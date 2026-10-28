"""Release metadata agrees before packaging; project citations stay version-neutral."""

import importlib.metadata
import os
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def source_project():
    path = ROOT / "pyproject.toml"
    if not path.is_file():
        pytest.skip("Source metadata is checked before the installed-wheel gate")
    return tomllib.loads(path.read_text())["project"]


def test_package_lock_and_installed_version_agree():
    project = source_project()
    locked = tomllib.loads((ROOT / "uv.lock").read_text())
    package = next(p for p in locked["package"] if p["name"] == project["name"])
    assert package["version"] == project["version"]
    assert importlib.metadata.version(project["name"]) == project["version"]
    from harness.web_fetch import __version__

    assert __version__ == project["version"]


def test_project_citations_do_not_claim_a_specific_release():
    source_project()
    cff = (ROOT / "CITATION.cff").read_text()
    assert not re.search(r"^(version|date-released):", cff, re.M)
    citations = []
    for path in [ROOT / "README.md", ROOT / "docs/README.zh-CN.md"]:
        text = path.read_text()
        block = re.search(r"```bibtex\n(.*?)```", text, re.S).group(1)
        assert not re.search(r"\bversion\s*=", block)
        url = re.search(r"url\s*=\s*\{([^}]+)\}", block).group(1)
        assert url in cff
        citations.append(block)
    assert citations[0] == citations[1]


def test_release_tag_matches_installed_identity():
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        pytest.skip("Tag identity is checked on release-tag CI runs")
    tag = os.environ["GITHUB_REF_NAME"]
    assert tag == "v" + importlib.metadata.version("browser-harness-eval")


def test_declared_benchmark_pins_match_lock_and_installation():
    import json

    from harness.lexbench_pin import validate_binding

    project = source_project()
    locked = tomllib.loads((ROOT / "uv.lock").read_text())
    for dependency in project["dependencies"]:
        if not dependency.startswith("lexbench-"):
            continue
        name = dependency.split(" @ ")[0]
        revision = validate_binding(ROOT, name)
        package = next(p for p in locked["package"] if p["name"] == name)
        installed = importlib.metadata.distribution(name)
        assert installed.version == package["version"], name
        origin = json.loads(installed.read_text("direct_url.json"))
        assert origin["vcs_info"]["commit_id"] == revision, name


def test_all_git_dependencies_resolve_to_locked_source_commits():
    from urllib.parse import parse_qs, urlsplit

    project = source_project()
    packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    dependencies = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        dependencies.extend(extra)
    for dependency in dependencies:
        if " @ git+" not in dependency:
            continue
        name, source = dependency.split(" @ git+", 1)
        repository, requested = source.rsplit("@", 1)
        package = next(p for p in packages if p["name"] == name)
        locked = urlsplit(package["source"]["git"])
        assert re.fullmatch(r"[0-9a-f]{40}", locked.fragment), name
        assert locked._replace(query="", fragment="").geturl() == repository, name
        assert parse_qs(locked.query).get("rev") == [requested], name
        if re.fullmatch(r"[0-9a-f]{40}", requested):
            assert locked.fragment == requested, name


def test_installed_benchmarks_match_their_frozen_catalogs():
    from importlib import import_module

    source_project()
    for module in ["lexbench_webfetch", "lexbench_websearch"]:
        import_module(module + ".versions").catalog_release()


def test_runtime_version_reads_installation_metadata():
    import subprocess
    import sys

    code = """
from unittest.mock import patch
with patch('importlib.metadata.version', return_value='9.8.7') as installed:
    import harness.web_fetch as package
    assert package.__version__ == '9.8.7'
    installed.assert_called_once_with('browser-harness-eval')
"""
    subprocess.run([sys.executable, "-c", code], check=True)
