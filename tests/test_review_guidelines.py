"""Contracts for complete OCR inputs, pinned updates and local verification."""

import errno
import json
from pathlib import Path
import subprocess
import sys

import pytest

from harness import review_guidelines as review

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/review_guidelines.py"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def repository(root, source=False):
    root.mkdir()
    git(root, "init", "-qb", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    if source:
        git(root, "remote", "add", "origin", "https://github.com/lexmount/ai-template-py.git")
    return root


def consumer(root):
    repository(root)
    (root / ".opencodereview").mkdir()
    (root / review.SUPPLEMENT).write_text("# Local checks\nKeep native scoring evidence.\n")
    (root / ".opencodereview/rule.json").write_text(json.dumps({"rules": [{
        "path": "**", "rule": review.ENTRY, "merge_system_rule": True,
    }]}))
    return root


def command(root, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], cwd=root,
        capture_output=True, text=True,
    )


@pytest.fixture
def repos(tmp_path):
    source = repository(tmp_path / "source", source=True)
    (source / "docs/references").mkdir(parents=True)
    (source / review.GUIDE).write_text("# Shared criteria\nPreserve existing supported behavior.\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "initial shared requirements")
    revision = git(source, "rev-parse", "HEAD")
    target = consumer(tmp_path / "target")
    review.sync(source, revision, [target])
    return source, target, revision


def test_repository_entry_contains_complete_shared_and_local_requirements():
    review.check(ROOT)
    shared = (ROOT / review.GUIDE).read_bytes()
    local = (ROOT / review.SUPPLEMENT).read_bytes()
    entry = (ROOT / review.ENTRY).read_bytes()
    assert entry.count(shared) == 1
    assert entry.count(local) == 1


def test_sync_uses_committed_policy_and_preserves_local_code_and_requirements(repos):
    source, target, revision = repos
    expected = (target / review.GUIDE).read_bytes()
    (source / review.GUIDE).write_text("uncommitted requirement")
    (source / "scripts").mkdir()
    (source / "scripts/review_guidelines.py").write_text("upstream tool")
    (target / "scripts").mkdir()
    tool = target / "scripts/review_guidelines.py"
    tool.write_text("repository-owned tool")
    local = (target / review.SUPPLEMENT).read_bytes()
    review.sync(source, revision, [target])
    assert (target / review.GUIDE).read_bytes() == expected
    assert (target / review.SUPPLEMENT).read_bytes() == local
    assert tool.read_text() == "repository-owned tool"
    review.check(target, source)


@pytest.mark.parametrize("path", [review.GUIDE, review.SUPPLEMENT, review.ENTRY])
def test_check_rejects_changed_inputs_without_rewriting_them(repos, path):
    _, target, _ = repos
    changed = (target / path).read_bytes() + b"\nNew requirement.\n"
    (target / path).write_bytes(changed)
    result = command(target, "check")
    assert result.returncode != 0
    assert (target / path).read_bytes() == changed


def test_render_updates_local_requirements_without_rewriting_source_lock(repos):
    _, target, _ = repos
    lock = (target / review.LOCK).read_bytes()
    shared = (target / review.GUIDE).read_bytes()
    local = b"# Revised local criteria\nReview evidence and ownership together.\n"
    (target / review.SUPPLEMENT).write_bytes(local)
    assert command(target, "render").returncode == 0
    assert local in (target / review.ENTRY).read_bytes()
    assert (target / review.GUIDE).read_bytes() == shared
    assert (target / review.LOCK).read_bytes() == lock
    assert command(target, "check").returncode == 0


def test_render_cannot_hide_drift_in_the_pinned_shared_guide(repos):
    _, target, _ = repos
    before = (target / review.ENTRY).read_bytes()
    (target / review.GUIDE).write_text("edited local copy")
    assert command(target, "render").returncode != 0
    assert (target / review.ENTRY).read_bytes() == before


@pytest.mark.parametrize("change", ["repository", "revision", "files", "version", "checksum", "shape"])
def test_invalid_source_lock_is_rejected(repos, change):
    _, target, _ = repos
    lock = json.loads((target / review.LOCK).read_text())
    if change == "repository":
        lock["repository"] = "another/repository"
    elif change == "revision":
        lock["revision"] = "main"
    elif change == "files":
        lock["files"] = {}
    elif change == "version":
        lock["version"] = 1
    elif change == "checksum":
        lock["files"][review.GUIDE] = "0" * 64
    else:
        lock = []
    (target / review.LOCK).write_text(json.dumps(lock))
    assert command(target, "check").returncode != 0


def test_missing_lock_is_rejected(repos):
    _, target, _ = repos
    (target / review.LOCK).unlink()
    assert command(target, "check").returncode != 0


def test_check_resolves_the_repository_from_a_nested_directory(repos):
    source, target, _ = repos
    nested = target / "scripts/nested"
    nested.mkdir(parents=True)
    result = command(nested, "check", "--source", source)
    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout


@pytest.mark.parametrize("url", [
    "git@github.com:lexmount/ai-template-py.git",
    "ssh://git@github.com/lexmount/ai-template-py.git",
    "https://github.com/LEXMOUNT/AI-TEMPLATE-PY.git/",
])
def test_source_checkout_accepts_standard_remote_urls_and_other_remote_names(repos, url):
    source, target, _ = repos
    git(source, "remote", "rename", "origin", "upstream")
    git(source, "remote", "set-url", "upstream", url)
    result = command(target, "check", "--source", source)
    assert result.returncode == 0, result.stderr


def test_missing_source_remote_has_a_clear_error(repos):
    source, target, _ = repos
    git(source, "remote", "remove", "origin")
    result = command(target, "check", "--source", source)
    assert result.returncode != 0
    assert "Configure a source checkout remote" in result.stderr
    assert "Traceback" not in result.stderr


def test_source_comparison_rejects_a_relabelled_revision(repos):
    source, target, _ = repos
    (source / review.GUIDE).write_text("# Changed requirements\n")
    git(source, "add", review.GUIDE)
    git(source, "commit", "-qm", "change requirements")
    lock = json.loads((target / review.LOCK).read_text())
    lock["revision"] = git(source, "rev-parse", "HEAD")
    (target / review.LOCK).write_text(json.dumps(lock))
    assert command(target, "check").returncode == 0
    result = command(target, "check", "--source", source)
    assert result.returncode != 0
    assert "Source mismatch" in result.stderr


@pytest.mark.parametrize("revision", ["main", "a" * 40])
def test_unavailable_or_moving_source_revision_leaves_the_target_unchanged(repos, revision):
    source, target, _ = repos
    before = {path: (target / path).read_bytes() for path in [review.GUIDE, review.ENTRY, review.LOCK]}
    assert command(target, "sync", "--source", source, "--ref", revision, target).returncode != 0
    assert {path: (target / path).read_bytes() for path in before} == before


def test_git_tree_is_not_accepted_as_a_source_commit(repos):
    source, target, _ = repos
    result = command(target, "sync", "--source", source, "--ref", git(source, "write-tree"), target)
    assert result.returncode != 0
    assert "must identify a commit" in result.stderr


@pytest.mark.parametrize("mode", ["disabled_builtin", "missing_fallback", "shadowed"])
def test_incomplete_effective_ocr_routes_are_rejected(repos, mode):
    _, target, _ = repos
    fallback = {"path": "**", "rule": review.ENTRY, "merge_system_rule": True}
    rules = [dict(fallback)]
    if mode == "disabled_builtin":
        rules[0]["merge_system_rule"] = False
    elif mode == "missing_fallback":
        rules[0]["path"] = "**/*.py"
    else:
        rules.insert(0, {"path": "**/*.py", "rule": review.SUPPLEMENT, "merge_system_rule": True})
    (target / ".opencodereview/rule.json").write_text(json.dumps({"rules": rules}))
    assert command(target, "check").returncode != 0


def test_additional_rules_follow_ocr_first_match_behavior(repos):
    source, target, revision = repos
    rules = [
        {"path": "**", "rule": "", "merge_system_rule": False},
        {"path": "**/*.py", "rule": review.ENTRY, "merge_system_rule": True},
        {"path": "**", "rule": review.ENTRY, "merge_system_rule": True},
        {"path": "**", "rule": review.SUPPLEMENT, "merge_system_rule": True},
    ]
    (target / ".opencodereview/rule.json").write_text(json.dumps({"rules": rules}))
    review.sync(source, revision, [target])
    assert command(target, "check").returncode == 0


def test_inline_rules_preserve_complete_guidance(repos):
    _, target, _ = repos
    body = (target / review.ENTRY).read_text()
    config = {"rules": [{"path": "**", "rule": body, "merge_system_rule": True}]}
    (target / ".opencodereview/rule.json").write_text(json.dumps(config))
    assert command(target, "check").returncode == 0


def test_invalid_later_target_is_rejected_before_any_changes(repos, tmp_path):
    source, target, revision = repos
    other = consumer(tmp_path / "other")
    (other / "docs").write_text("not a directory")
    before = (target / review.LOCK).read_bytes()
    with pytest.raises(ValueError, match="must be a directory"):
        review.sync(source, revision, [target, other])
    assert (target / review.LOCK).read_bytes() == before
    assert not (other / review.LOCK).exists()


def test_symlinked_managed_paths_cannot_overwrite_external_files(repos, tmp_path):
    source, target, revision = repos
    external = tmp_path / "external.md"
    external.write_text("external content")
    (target / review.GUIDE).unlink()
    (target / review.GUIDE).symlink_to(external)
    with pytest.raises(ValueError, match="symlinks"):
        review.sync(source, revision, [target])
    assert external.read_text() == "external content"


def test_failed_staging_preserves_the_existing_files(repos, monkeypatch):
    source, target, revision = repos
    paths = [review.GUIDE, review.ENTRY, review.LOCK]
    before = {path: (target / path).read_bytes() for path in paths}
    write = Path.write_bytes

    def fail(path, data):
        if path.name == Path(review.ENTRY).name:
            write(path, data[:5])
            raise OSError(errno.ENOSPC, "No space left")
        return write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_bytes", fail)
        with pytest.raises(OSError, match="No space left"):
            review.sync(source, revision, [target])
    assert {path: (target / path).read_bytes() for path in paths} == before
    review.check(target, source)


def test_partial_batch_failure_is_detected_and_retry_repairs_it(repos, tmp_path, monkeypatch):
    source, target, revision = repos
    other = consumer(tmp_path / "other")
    review.sync(source, revision, [other])
    (source / review.GUIDE).write_text("# Updated policy\nA newly agreed criterion.\n")
    git(source, "add", review.GUIDE)
    git(source, "commit", "-qm", "update shared policy")
    updated = git(source, "rev-parse", "HEAD")
    replace = Path.replace

    def fail(path, destination):
        if destination == other / review.ENTRY:
            raise OSError("simulated replacement failure")
        return replace(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="simulated replacement failure"):
            review.sync(source, updated, [target, other])
    review.check(target, source)
    assert (other / review.GUIDE).read_bytes() == (target / review.GUIDE).read_bytes()
    with pytest.raises(ValueError, match="Local drift"):
        review.check(other)
    review.sync(source, updated, [target, other])
    review.check(other, source)
