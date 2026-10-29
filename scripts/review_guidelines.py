"""Synchronize pinned review guidance and verify its local integrity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory


REPOSITORY = "lexmount/ai-template-py"
FILES = ("docs/references/review-guidelines.md", "scripts/review_guidelines.py")
LOCK = "review-guidelines.lock.json"
# A consumer's copy of the guide may end with its own requirements after this
# line. The lock and source comparison cover only the shared text above it.
SUPPLEMENT = b"<!-- Repository review supplement: maintained locally; synchronization preserves the text below. -->"


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    ).stdout


def repository_root(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve()


def is_source(root: Path) -> bool:
    remote = git(root, "remote", "get-url", "origin").decode().strip().rstrip("/")
    remote = remote.removesuffix(".git").replace("ssh://git@github.com/", "git@github.com:")
    return remote in (
        f"git@github.com:{REPOSITORY}",
        f"https://github.com/{REPOSITORY}",
    )


def shared_part(path: str, content: bytes) -> bytes:
    if path != FILES[0]:
        return content
    shared, marker, _ = content.partition(SUPPLEMENT)
    # Blank lines before the marker separate the sections and are not shared text.
    return shared.rstrip(b"\n") + b"\n" if marker else shared


def local_part(path: str, content: bytes) -> bytes:
    index = content.find(SUPPLEMENT) if path == FILES[0] else -1
    return b"" if index < 0 else b"\n" + content[index:]


def managed_path(root: Path, relative: str) -> Path:
    path = root / relative
    if path.exists() and not path.is_file():
        raise ValueError(f"Managed destination must be a file: {path}")
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError(f"Managed paths must not be symlinks: {candidate}")
        if candidate != path and candidate.exists() and not candidate.is_dir():
            raise ValueError(f"Managed parent must be a directory: {candidate}")
    return path


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_files(source: Path, revision: str) -> dict[str, bytes]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Use a full 40-character source commit, not a moving ref")
    if not is_source(source):
        raise ValueError(f"Source origin must be {REPOSITORY}")
    if git(source, "cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("The source revision must identify a commit")
    contents = {path: git(source, "show", f"{revision}:{path}") for path in FILES}
    if local_part(FILES[0], contents[FILES[0]]):
        raise ValueError("The canonical guide must not contain a repository supplement")
    return contents


def check_routing(root: Path) -> None:
    config_path = root / ".opencodereview/rule.json"
    if not config_path.exists():
        raise ValueError("Missing .opencodereview/rule.json")
    rules = json.loads(config_path.read_text())["rules"]
    if rules != [{"path": "**", "rule": FILES[0], "merge_system_rule": True}]:
        raise ValueError("OCR must load the shared guide and retain built-in rules")


def check(root: Path, source: Path | None = None) -> None:
    lock_path = managed_path(root, LOCK)
    if not lock_path.exists():
        if not is_source(root):
            raise ValueError(f"Missing {LOCK}; synchronize from {REPOSITORY}")
        for path in FILES:
            content = managed_path(root, path).read_bytes()
            if not content.strip():
                raise ValueError(f"Empty source file: {path}")
            if local_part(path, content):
                raise ValueError("The canonical guide must not contain a repository supplement")
    else:
        lock = json.loads(lock_path.read_text())
        if lock.get("repository") != REPOSITORY or lock.get("version") != 1:
            raise ValueError("Unsupported review source lock")
        revision = lock["revision"]
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("The source lock must pin a full commit")
        if set(lock["files"]) != set(FILES):
            raise ValueError("The lock must cover the guide and synchronization tool")
        upstream = source_files(source, revision) if source is not None else None
        for path in FILES:
            local = shared_part(path, managed_path(root, path).read_bytes())
            if digest(local) != lock["files"][path]:
                raise ValueError(f"Local drift: {path}; synchronize, do not edit the copy")
            if upstream is not None and local != upstream[path]:
                raise ValueError(f"Source mismatch at {revision}: {path}")
    check_routing(root)
    print(f"Review guidance verified: {root}")


def sync(source: Path, revision: str, targets: list[Path]) -> None:
    contents = source_files(source, revision)
    lock = {
        "version": 1,
        "repository": REPOSITORY,
        "revision": revision,
        "files": {path: digest(data) for path, data in contents.items()},
    }
    prepared = []
    # Validate every destination before writing; repositories are then updated in turn.
    for root in targets:
        if is_source(root):
            raise ValueError("Edit the canonical source directly; do not sync over it")
        payloads = {**contents, LOCK: (json.dumps(lock, indent=2) + "\n").encode()}
        for path in payloads:
            managed_path(root, path)
        guide = root / FILES[0]
        if guide.exists():
            payloads[FILES[0]] += local_part(FILES[0], guide.read_bytes())
        check_routing(root)
        prepared.append((root, payloads))
    for root, payloads in prepared:
        with TemporaryDirectory(prefix=".review-guidelines-", dir=root) as directory:
            staged = Path(directory)
            for path, data in payloads.items():
                (staged / Path(path).name).write_bytes(data)
            # Replace complete files on the same filesystem, publishing the lock last.
            for path in payloads:
                destination = managed_path(root, path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                (staged / Path(path).name).replace(destination)
        check(root, source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("check", help="Check the pinned local files offline")
    verify.add_argument("--source", type=Path, help="Also compare the pinned Git source")
    update = commands.add_parser("sync", help="Update one or more repository checkouts")
    update.add_argument("--source", type=Path, required=True)
    update.add_argument("--ref", required=True, help="Full source commit")
    update.add_argument("targets", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check":
            source = repository_root(args.source) if args.source else None
            check(repository_root(Path.cwd()), source)
        else:
            sync(repository_root(args.source), args.ref, [repository_root(p) for p in args.targets])
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"Review guidance error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
