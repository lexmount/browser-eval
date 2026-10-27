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
