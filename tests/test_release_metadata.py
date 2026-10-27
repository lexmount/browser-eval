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


def test_readme_current_package_version_matches_installed_release():
    project = source_project()
    for path in [ROOT / "README.md", ROOT / "docs/README.zh-CN.md"]:
        text = path.read_text()
        section = re.search(r"^## (?:Versions|版本)\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        assert section is not None, path
        declared = re.search(r"`([0-9]+\.[0-9]+\.[0-9]+)`", section[1])
        assert declared is not None, path
        assert declared[1] == project["version"], path


def test_declared_benchmark_pins_match_lock_and_installation():
    import json

    project = source_project()
    locked = tomllib.loads((ROOT / "uv.lock").read_text())
    for dependency in project["dependencies"]:
        if not dependency.startswith("lexbench-"):
            continue
        name, location = dependency.split(" @ ")
        revision = location.rsplit("@", 1)[1]
        assert re.fullmatch(r"[a-f0-9]{40}", revision), name
        package = next(p for p in locked["package"] if p["name"] == name)
        assert package["source"]["git"].split("#")[1] == revision, name
        installed = importlib.metadata.distribution(name)
        assert installed.version == package["version"], name
        origin = json.loads(installed.read_text("direct_url.json"))
        assert origin["vcs_info"]["commit_id"] == revision, name


def test_current_release_dependency_table_matches_installed_benchmarks():
    project = source_project()
    guide = (ROOT / "docs/releases.md").read_text()
    row = next(
        line for line in guide.splitlines() if line.startswith("| [" + project["version"] + "](")
    )
    for label, module in [("Fetch", "lexbench_webfetch"), ("Search", "lexbench_websearch")]:
        from importlib import import_module

        edition = import_module(module + ".versions").catalog_release()["version"]
        tooling = importlib.metadata.version(module.replace("_", "-"))
        assert f"{label} benchmark {edition} / tooling {tooling}" in row
