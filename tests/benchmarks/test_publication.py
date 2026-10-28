"""Dataset releases preserve committed inputs and reject broken populations."""
import hashlib
import json
import subprocess
import tarfile

import pytest

from harness.benchmarks.publication import DatasetRelease


def git(checkout, *args):
    return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.org", *args],
                          cwd=checkout, check=True, capture_output=True, text=True).stdout.strip()


def checkout(tmp_path, track, defect=None):
    root = tmp_path / "source"
    root.mkdir()
    prefix = root / f"src/lexbench_{track}/definitions"
    prefix.mkdir(parents=True)
    cases = [{"id": "first", "query": "Which source?", "url": "https://example.org", "requirements": ["a", "b"]}]
    if defect == "duplicate":
        cases.append(cases[0])
    raw = json.dumps({"cases": cases}).encode()
    path = "tasks.json"
    (prefix / path).write_bytes(raw)
    binding = {"path": path, "count": 2 if defect == "count" else len(cases),
               "sha256": "0" * 64 if defect == "hash" else hashlib.sha256(raw).hexdigest()}
    registry = {"datasets": {"sample": binding}}
    if track == "webfetch":
        registry = {"suites": [{"id": "sample", "directory": ".", "case_count": binding["count"],
                                "files": {"tasks.json": binding["sha256"]}}]}
        # Keep the directory spelling identical to real suite registries.
        (prefix / "evaluation").mkdir()
        (prefix / "evaluation/tasks.json").write_bytes(raw)
        registry["suites"][0]["directory"] = "evaluation"
    (prefix / "registry.json").write_text(json.dumps(registry))
    for name in ["LICENSE", "NOTICE", "THIRD_PARTY.md"]:
        (root / name).write_text("Retain this notice.\n")
    if defect == "symlink":
        (prefix / "linked.json").symlink_to("tasks.json")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture")
    return root, git(root, "rev-parse", "HEAD"), cases


@pytest.mark.parametrize("track", ["webfetch", "websearch"])
def test_release_preserves_tasks_not_working_tree_or_private_files(tmp_path, track):
    root, revision, cases = checkout(tmp_path, track)
    (root / ".env").write_text("PRIVATE=not-publishable")
    (root / f"src/lexbench_{track}/definitions/tasks.json").write_text("modified after commit")
    release = DatasetRelease.from_git(root, track, revision)
    left, right = tmp_path / "left", tmp_path / "right"
    manifest = release.build(left)
    release.build(right)
    assert (left / "definitions.tar.gz").read_bytes() == (right / "definitions.tar.gz").read_bytes()
    rows = [json.loads(line) for line in (left / "data/sample.jsonl").read_text().splitlines()]
    assert [json.loads(row["task"]) for row in rows] == cases
    assert manifest["source_revision"] == revision
    assert not (left / ".env").exists()
    with tarfile.open(left / "definitions.tar.gz") as archive:
        for item in archive:
            assert archive.extractfile(item).read() == (left / item.name).read_bytes()
    for name, binding in manifest["files"].items():
        assert hashlib.sha256((left / name).read_bytes()).hexdigest() == binding["sha256"]


@pytest.mark.parametrize("track", ["webfetch", "websearch"])
@pytest.mark.parametrize("defect", ["hash", "count", "duplicate", "symlink"])
def test_invalid_release_is_rejected(tmp_path, track, defect):
    root, revision, _ = checkout(tmp_path, track, defect)
    with pytest.raises(ValueError):
        DatasetRelease.from_git(root, track, revision).build(tmp_path / "release")


def test_release_requires_commit_and_empty_destination(tmp_path):
    root, revision, _ = checkout(tmp_path, "websearch")
    with pytest.raises(ValueError, match="full Git commit"):
        DatasetRelease.from_git(root, "websearch", "main")
    release = DatasetRelease.from_git(root, "websearch", revision)
    release.build(tmp_path / "release")
    with pytest.raises(ValueError, match="empty"):
        release.build(tmp_path / "release")


def test_jsonl_derivative_retains_upstream_identifiers_and_source(tmp_path):
    root, revision, _ = checkout(tmp_path, "websearch")
    release = DatasetRelease.from_git(root, "websearch", revision)
    records = [{"track_id": "upstream-1", "url": "https://example.org", "html": "<main>Content</main>",
                "groundtruth_content": "Content"}]
    raw = b"".join((json.dumps(row) + "\n").encode() for row in records)
    files = dict(release.files)
    files["definitions/records.jsonl"] = raw
    files["definitions/registry.json"] = json.dumps({"datasets": {"corrected": {
        "path": "records.jsonl", "format": "jsonl", "id_field": "track_id", "count": 1,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }}}).encode()
    derived = DatasetRelease("webmainbench", revision, files, "https://github.com/lexmount/WebMainBench")
    result = derived.build(tmp_path / "release")
    exported = json.loads((tmp_path / "release/data/corrected.jsonl").read_text())
    assert exported["id"] == "upstream-1"
    assert json.loads(exported["task"]) == records[0]
    assert result["source_repository"] == "https://github.com/lexmount/WebMainBench"
