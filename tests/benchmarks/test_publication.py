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


@pytest.mark.parametrize("missing", ["yaml", "card"])
@pytest.mark.parametrize("existing_empty", [False, True])
def test_build_prerequisites_fail_before_output_and_allow_retry(
    tmp_path, monkeypatch, missing, existing_empty
):
    import builtins
    from pathlib import Path

    from harness.benchmarks import publication

    root, revision, _ = checkout(tmp_path, "websearch")
    release = DatasetRelease.from_git(root, "websearch", revision)
    output = tmp_path / "release"
    if existing_empty:
        output.mkdir()
    original_import = builtins.__import__
    original_read = Path.read_text

    def blocked_import(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("publishing extra is unavailable")
        return original_import(name, *args, **kwargs)

    def blocked_card(path, *args, **kwargs):
        if path == publication.CARDS / "websearch.md":
            raise OSError("card is unreadable")
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as failure:
        if missing == "yaml":
            failure.setattr(builtins, "__import__", blocked_import)
        else:
            failure.setattr(Path, "read_text", blocked_card)
        with pytest.raises((ModuleNotFoundError, OSError)):
            release.build(output)
    assert not output.exists() or list(output.iterdir()) == []
    result = release.build(output)
    assert (output / "README.md").is_file()
    assert result["archive"]["path"] == "definitions.tar.gz"


@pytest.mark.parametrize("inline_card", [False, True])
def test_release_utf8_roundtrip_under_ascii_locale(tmp_path, inline_card):
    import os
    import sys

    from harness.benchmarks import publication

    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "metadata.json").write_bytes((publication.CARDS / "metadata.json").read_bytes())
    (cards / "websearch.md").write_text("# 评测 café\n\n{{dataset_summary}}\n", encoding="utf-8")
    script = r'''
import hashlib
import json
import locale
import sys
from pathlib import Path
from harness.benchmarks import publication
assert sys.flags.utf8_mode == 0
assert locale.getencoding().lower() not in {'utf-8', 'utf8'}
publication.CARDS = Path(sys.argv[1])
raw = json.dumps({'cases': [{'id': 'one', 'query': '\u68c0\u7d22 caf\u00e9'}]}, ensure_ascii=False).encode('utf-8')
registry = {'datasets': {'sample': {'path': 'tasks.json', 'count': 1,
                                  'sha256': hashlib.sha256(raw).hexdigest()}}}
body = '# \u8bc4\u6d4b caf\u00e9\n' if sys.argv[3] == 'True' else None
release = publication.DatasetRelease('websearch', 'a' * 40, {
    'definitions/registry.json': json.dumps(registry).encode('utf-8'),
    'definitions/tasks.json': raw,
}, card_body=body, language_annotations={})
output = Path(sys.argv[2])
release.build(output)
assert '\u8bc4\u6d4b caf\u00e9' in (output / 'README.md').read_text(encoding='utf-8')
assert (output / 'definitions/tasks.json').read_bytes() == raw
viewer = json.loads((output / 'data/sample.jsonl').read_bytes())
assert json.loads(viewer['task'])['query'] == '\u68c0\u7d22 caf\u00e9'
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(cards), str(tmp_path / "release"), str(inline_card)],
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("claimed", ["websearch", "trajectory-replay"])
def test_hub_track_identity_comes_from_committed_manifest(tmp_path, claimed):
    root, _, _ = checkout(tmp_path, "websearch")
    (root / "src/lexbench_websearch/definitions").rename(root / "definitions")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "browser-eval-dataset-release/1", "track": "websearch",
    }), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Hub layout")
    revision = git(root, "rev-parse", "HEAD")
    # A dirty working-tree claim must neither repair nor break the committed identity.
    manifest.write_text(json.dumps({"track": "trajectory-replay"}), encoding="utf-8")
    output = tmp_path / "release"
    if claimed == "websearch":
        release = DatasetRelease.from_git(root, claimed, revision)
        assert release.build(output)["track"] == "websearch"
    else:
        with pytest.raises(ValueError, match="track"):
            DatasetRelease.from_git(root, claimed, revision).build(output)
        assert not output.exists()


def test_hub_track_cannot_be_supplied_by_uncommitted_manifest(tmp_path):
    root, _, _ = checkout(tmp_path, "websearch")
    (root / "src/lexbench_websearch/definitions").rename(root / "definitions")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Hub without identity")
    revision = git(root, "rev-parse", "HEAD")
    (root / "manifest.json").write_text(json.dumps({
        "schema": "browser-eval-dataset-release/1", "track": "websearch",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        DatasetRelease.from_git(root, "websearch", revision)


def test_runtime_settings_survive_original_source_publication_and_hub_rebuild(tmp_path, monkeypatch):
    from harness.benchmarks import publication
    root, _, _ = checkout(tmp_path, 'webmainbench')
    registry = root / 'src/lexbench_webmainbench/definitions/registry.json'
    values = json.loads(registry.read_bytes())
    scorer = {'repository': 'https://github.com/lexmount/WebMainBench.git', 'revision': 'b' * 40}
    values.update(default_dataset='sample', scorer=scorer)
    registry.write_text(json.dumps(values))
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Bind runtime choices')
    revision = git(root, 'rev-parse', 'HEAD')
    output = tmp_path / 'published'
    monkeypatch.setattr('sys.argv', ['publish', '--checkout', str(root), '--track', 'webmainbench',
                                    '--revision', revision, '--output', str(output)])
    publication.main()
    manifest = json.loads((output / 'manifest.json').read_bytes())
    assert manifest['scorer'] == scorer
    assert manifest['default_dataset'] == 'sample'
    git(output, 'init', '-q')
    git(output, 'add', '.')
    git(output, 'commit', '-qm', 'Published edition')
    published_revision = git(output, 'rev-parse', 'HEAD')
    dirty = json.loads((output / 'manifest.json').read_bytes())
    dirty['scorer']['revision'] = 'c' * 40
    (output / 'manifest.json').write_text(json.dumps(dirty))
    rebuilt = DatasetRelease.from_git(output, 'webmainbench', published_revision).build(tmp_path / 'rebuilt')
    assert rebuilt['scorer'] == scorer
    assert rebuilt['default_dataset'] == 'sample'


@pytest.mark.parametrize('settings', [
    {'default_dataset': 'missing'},
    {'scorer': {'repository': 'https://github.com/example/scorer', 'revision': 'main'}},
])
def test_invalid_runtime_choices_fail_before_output(tmp_path, settings):
    from dataclasses import replace
    root, revision, _ = checkout(tmp_path, 'websearch')
    release = replace(DatasetRelease.from_git(root, 'websearch', revision), **settings)
    output = tmp_path / 'release'
    with pytest.raises(ValueError, match='Default dataset|Scorer requires'):
        release.build(output)
    assert not output.exists()


def test_publish_opens_a_review_pr_without_updating_main_or_creating_tags(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace
    from harness.benchmarks import publication

    root, revision, _ = checkout(tmp_path, 'websearch')
    output = tmp_path / 'proposal'
    calls = []

    class ReviewApi:
        def repo_info(self, *, repo_id, repo_type):
            assert (repo_id, repo_type) == ('Lexmount/Test-Dataset', 'dataset')
            return SimpleNamespace(sha='a' * 40, siblings=[
                SimpleNamespace(rfilename='.gitattributes'),
                SimpleNamespace(rfilename='data/retired.jsonl')])

        def upload_folder(self, **kwargs):
            assert kwargs['create_pr'] is True
            assert kwargs['parent_commit'] == 'a' * 40
            assert kwargs['delete_patterns'] == ['data/retired.jsonl']
            assert (kwargs['folder_path'] / 'manifest.json').is_file()
            calls.append(kwargs)
            return SimpleNamespace(oid='b' * 40,
                pr_url='https://huggingface.co/datasets/Lexmount/Test-Dataset/discussions/7')
        # No main-branch commit, merge, or tag operation is available here.

    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(HfApi=ReviewApi))
    monkeypatch.setattr(sys, 'argv', ['publication', '--checkout', str(root), '--track', 'websearch',
        '--revision', revision, '--output', str(output), '--publish', 'Lexmount/Test-Dataset'])
    publication.main()
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(calls) == 1
    assert result['state'] == 'review_pending'
    assert result['revision'] == 'b' * 40
    assert result['pull_request'].endswith('/discussions/7')
