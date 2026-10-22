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
