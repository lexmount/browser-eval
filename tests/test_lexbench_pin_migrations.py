"""Exercise public dependency migration with real local Git sources and uv."""

import hashlib
import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from harness.lexbench_pin import bump, project_pin, validate_binding

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def package(root, name, definitions):
    module = name.replace("-", "_")
    source = root / "src" / module
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    (source / "inputs.py").write_text(
        "from pathlib import Path\n"
        "def definition_path(relative):\n"
        '    return Path(__file__).parent / "definitions" / relative\n'
    )
    for name_in_package, data in definitions.items():
        path = source / "definitions" / name_in_package
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\nrequires-python = ">=3.11"\n'
        '[build-system]\nrequires = ["setuptools>=77"]\nbuild-backend = "setuptools.build_meta"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        f'[tool.setuptools.package-data]\n{module} = ["definitions/*.json", "definitions/evaluation/*/*.json"]\n'
    )
    return source / "definitions"


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("benchmark-family")
    family = root / "family"
    family.mkdir()
    task = {"id": "tasks", "cases": [{"id": "first"}]}
    fetch = package(family, "lexbench-webfetch", {"evaluation/example/tasks.json": task})
    raw = json.dumps({"id": "questions", "cases": [{"id": "query"}]}).encode()
    registry = {
        "datasets": {
            "questions": {
                "path": "questions.json",
                "count": 1,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        }
    }
    search = package(
        family / "packages/websearch",
        "lexbench-websearch",
        {"registry.json": registry, "questions.json": json.loads(raw)},
    )
    with (family / "pyproject.toml").open("a") as stream:
        stream.write('\n[tool.uv.workspace]\nmembers = ["packages/*"]\n')
    git(family, "init", "-b", "main")
    git(family, "add", ".")
    git(
        family,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "Initial family",
    )
    before = git(family, "rev-parse", "HEAD")
    (family / "revision.txt").write_text("A tooling-only successor")
    git(family, "add", ".")
    git(
        family,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "Successor",
    )
    after = git(family, "rev-parse", "HEAD")
    standalone = root / "standalone"
    standalone.mkdir()
    package(
        standalone,
        "lexbench-websearch",
        {"registry.json": registry, "questions.json": json.loads(raw)},
    )
    git(standalone, "init", "-b", "main")
    git(standalone, "add", ".")
    git(
        standalone,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "Standalone Search",
    )
    return family, before, after, standalone, git(standalone, "rev-parse", "HEAD"), fetch, search


def consumer(root, sources):
    family, before, _, _, _, _, _ = sources
    url = family.as_uri()
    (root / "evaluation").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "0.0.0"\nrequires-python = ">=3.11"\ndependencies = [\n'
        f'  "lexbench-webfetch @ git+{url}@{before}",\n'
        f'  "lexbench-websearch @ git+{url}@{before}#subdirectory=packages/websearch",\n]\n'
    )
    for name in ["README.md", "LICENSE", "NOTICE", "THIRD_PARTY.md"]:
        (root / name).write_text("Test fixture\n")
    (root / ".python-version").write_text("3.11\n")
    subprocess.run(["uv", "lock", "--project", str(root)], check=True, capture_output=True)
    for name in ["lexbench-webfetch", "lexbench-websearch"]:
        (root / "evaluation" / f"{name}.json").write_text(
            json.dumps(
                {
                    "repository": url,
                    "revision": before,
                    **(
                        {"subdirectory": "packages/websearch", "datasets": {}}
                        if "search" in name
                        else {"suites": []}
                    ),
                }
            )
        )


@pytest.mark.parametrize("selected", ["lexbench-webfetch", "lexbench-websearch"])
def test_one_family_package_can_update_without_changing_its_sibling(tmp_path, sources, selected):
    consumer(tmp_path, sources)
    _, before, after, *_ = sources
    sibling = "lexbench-websearch" if selected == "lexbench-webfetch" else "lexbench-webfetch"
    sibling_binding = (tmp_path / "evaluation" / f"{sibling}.json").read_bytes()
    bump(tmp_path, after, benchmark=selected)
    assert validate_binding(tmp_path, selected) == after
    assert validate_binding(tmp_path, sibling) == before
    assert (tmp_path / "evaluation" / f"{sibling}.json").read_bytes() == sibling_binding


def test_make_can_clear_a_subdirectory_when_migrating_to_a_root_package(tmp_path, sources):
    consumer(tmp_path, sources)
    _, before, _, standalone, revision, *_ = sources
    (tmp_path / "scripts").mkdir()
    (tmp_path / "harness").mkdir()
    shutil.copyfile(ROOT / "scripts/bump_lexbench.py", tmp_path / "scripts/bump_lexbench.py")
    shutil.copyfile(ROOT / "harness/lexbench_pin.py", tmp_path / "harness/lexbench_pin.py")
    subprocess.run(
        [
            "make",
            "-f",
            str(ROOT / "Makefile"),
            "bump-lexbench",
            f"REV={revision}",
            "BENCHMARK=lexbench-websearch",
            f"REPOSITORY={standalone.as_uri()}",
            "SUBDIRECTORY=",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert validate_binding(tmp_path, "lexbench-websearch") == revision
    assert validate_binding(tmp_path, "lexbench-webfetch") == before
    assert (
        project_pin(tomllib.loads((tmp_path / "pyproject.toml").read_text()), "lexbench-websearch")[
            2
        ]
        == ""
    )
    assert "subdirectory" not in json.loads(
        (tmp_path / "evaluation/lexbench-websearch.json").read_text()
    )
