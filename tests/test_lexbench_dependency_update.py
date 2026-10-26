"""Dependency updates bind the installed definitions and fail before checkout edits."""
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.lexbench_pin import (
    binding_changes, bump, refresh_binding, require_merged_revision, validate_binding,
)

ROOT = Path(__file__).resolve().parents[1]


def test_current_dependency_has_one_revision():
    validate_binding(ROOT)


def test_search_dependency_binds_project_lock_and_installed_datasets(tmp_path):
    from lexbench_websearch.inputs import registry
    for name in ['pyproject.toml', 'uv.lock', 'evaluation/lexbench-websearch.json']:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    revision = validate_binding(tmp_path, 'lexbench-websearch')
    path = tmp_path / 'evaluation/lexbench-websearch.json'
    binding = json.loads(path.read_text())
    assert binding['datasets'] == registry()['datasets']
    binding['revision'] = '0' * 40
    path.write_text(json.dumps(binding))
    with pytest.raises(ValueError, match='disagree'):
        validate_binding(tmp_path, 'lexbench-websearch')
    assert len(revision) == 40


@pytest.mark.parametrize("name", ["pyproject.toml", "uv.lock", "evaluation/lexbench-webfetch.json"])
def test_mismatched_revision_rejected(tmp_path, name):
    (tmp_path / "evaluation").mkdir()
    for relative in ("pyproject.toml", "uv.lock", "evaluation/lexbench-webfetch.json"):
        shutil.copyfile(ROOT / relative, tmp_path / relative)
    revision = validate_binding(tmp_path)
    path = tmp_path / name
    path.write_text(path.read_text().replace(revision, "a" * 40))
    with pytest.raises(ValueError, match="make bump-lexbench"):
        validate_binding(tmp_path)


def test_refresh_captures_changed_tasks_and_hashes_without_mutating_input(tmp_path):
    directory = tmp_path / "evaluation/example"
    directory.mkdir(parents=True)
    raw = json.dumps({"id": "new-suite", "cases": [{"id": "a"}, {"id": "b"}]}).encode()
    (directory / "tasks.json").write_bytes(raw)
    binding = {"revision": "old", "definition_files": {"evaluation/example/tasks.json": "old"},
               "suites": [{"id": "example", "historical_directory": "evaluation/example",
                           "dataset_id": "old", "case_count": 1, "files": {"tasks.json": "old"}}]}
    original = copy.deepcopy(binding)
    result = refresh_binding(binding, "b" * 40, tmp_path)
    assert binding == original
    assert result["suites"][0]["case_count"] == 2
    assert result["suites"][0]["dataset_id"] == "new-suite"
    assert result["definition_files"]["evaluation/example/tasks.json"] == hashlib.sha256(raw).hexdigest()
    assert result["suites"][0]["files"]["tasks.json"] == hashlib.sha256(raw).hexdigest()
    assert any("case_count: 1 -> 2" in line for line in binding_changes(binding, result))


@pytest.mark.parametrize("bind_release", [False, True])
def test_search_refresh_loads_catalog_and_preserves_dataset_hash_guards(tmp_path, bind_release):
    from lexbench_websearch.inputs import DEFINITIONS
    shutil.copytree(DEFINITIONS, tmp_path / "definitions")
    definitions = tmp_path / "definitions"
    binding = {"revision": "old", "datasets": {}}
    if bind_release:
        binding["definition_files"] = {"benchmark-release.json": "old"}
    updated = refresh_binding(binding, "a" * 40, definitions)
    assert updated["datasets"] == json.loads((definitions / "registry.json").read_bytes())["datasets"]
    assert binding["revision"] == "old"
    if bind_release:
        assert updated["definition_files"]["benchmark-release.json"] == hashlib.sha256(
            (definitions / "benchmark-release.json").read_bytes()
        ).hexdigest()
    dataset = definitions / next(iter(updated["datasets"].values()))["path"]
    dataset.write_bytes(dataset.read_bytes() + b" ")
    with pytest.raises(ValueError, match="does not bind its dataset"):
        refresh_binding(binding, "a" * 40, definitions)


def test_rejected_update_leaves_all_bindings_unchanged(tmp_path, monkeypatch):
    (tmp_path / "evaluation").mkdir()
    names = ("pyproject.toml", "uv.lock", "evaluation/lexbench-webfetch.json")
    for name in names:
        shutil.copyfile(ROOT / name, tmp_path / name)
    before = {name: (tmp_path / name).read_bytes() for name in names}
    def reject(*args):
        raise ValueError("not merged")
    monkeypatch.setattr("harness.lexbench_pin.require_merged_revision", reject)
    with pytest.raises(ValueError, match="not merged"):
        bump(tmp_path, "a" * 40)
    assert before == {name: (tmp_path / name).read_bytes() for name in names}


def test_upstream_check_accepts_ancestor_and_rejects_unmerged_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()
    git("init", "-b", "main")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "main")
    merged = git("rev-parse", "HEAD")
    git("checkout", "-b", "unmerged")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "unmerged")
    unmerged = git("rev-parse", "HEAD")
    require_merged_revision(str(repo), merged, tmp_path / "accepted")
    with pytest.raises(ValueError, match="not in upstream main"):
        require_merged_revision(str(repo), unmerged, tmp_path / "rejected")


def family_binding(root, *, directory='packages/websearch'):
    revision = 'a' * 40
    repository = 'https://github.com/lexmount/lexbench-web-retrieval.git'
    (root / 'evaluation').mkdir(exist_ok=True)
    (root / 'pyproject.toml').write_text(
        '[project]\ndependencies = ["lexbench-websearch @ git+'
        + repository + '@' + revision + '#subdirectory=' + directory + '"]\n')
    (root / 'uv.lock').write_text(
        '[[package]]\nname = "lexbench-websearch"\nsource = { git = "'
        + repository + '?rev=' + revision + '&subdirectory=' + directory + '#' + revision + '" }\n')
    (root / 'evaluation/lexbench-websearch.json').write_text(json.dumps({
        'repository': repository.removesuffix('.git'), 'revision': revision,
        'subdirectory': directory,
    }))
    return revision


def test_family_package_pin_binds_repository_revision_and_subdirectory(tmp_path):
    revision = family_binding(tmp_path)
    assert validate_binding(tmp_path, 'lexbench-websearch') == revision


@pytest.mark.parametrize('changed', ['project', 'lock', 'binding'])
def test_family_package_cannot_silently_select_another_subdirectory(tmp_path, changed):
    family_binding(tmp_path)
    relative = {'project': 'pyproject.toml', 'lock': 'uv.lock',
                'binding': 'evaluation/lexbench-websearch.json'}[changed]
    path = tmp_path / relative
    path.write_text(path.read_text().replace('packages/websearch', 'packages/another'))
    with pytest.raises(ValueError, match='disagree'):
        validate_binding(tmp_path, 'lexbench-websearch')


@pytest.mark.parametrize('directory', ['../websearch', '/packages/websearch', 'packages/../websearch'])
def test_family_source_requires_a_contained_package_directory(tmp_path, directory):
    family_binding(tmp_path, directory=directory)
    with pytest.raises(ValueError, match='subdirectory'):
        validate_binding(tmp_path, 'lexbench-websearch')
