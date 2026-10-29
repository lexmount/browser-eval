"""Synchronize pinned review guidance and verify its local integrity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


REPOSITORY = "lexmount/ai-template-py"
FILES = ("docs/references/review-guidelines.md", "scripts/review_guidelines.py")
LOCK = "review-guidelines.lock.json"


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True
    ).stdout


def is_source(root: Path) -> bool:
    remote = git(root, "remote", "get-url", "origin").decode().strip()
    return remote.removesuffix(".git") in (
        f"git@github.com:{REPOSITORY}",
        f"https://github.com/{REPOSITORY}",
    )


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
    return {path: git(source, "show", f"{revision}:{path}") for path in FILES}


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
            if not managed_path(root, path).read_bytes().strip():
                raise ValueError(f"Empty source file: {path}")
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
            local = managed_path(root, path).read_bytes()
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
    payloads = {**contents, LOCK: (json.dumps(lock, indent=2) + "\n").encode()}
    # Validate every destination before changing any of them.
    for root in targets:
        if is_source(root):
            raise ValueError("Edit the canonical source directly; do not sync over it")
        for path in payloads:
            managed_path(root, path)
        check_routing(root)
    for root in targets:
        for path, data in payloads.items():
            destination = managed_path(root, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
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
            check(Path.cwd().resolve(), args.source.resolve() if args.source else None)
        else:
            sync(args.source.resolve(), args.ref, [p.resolve() for p in args.targets])
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"Review guidance error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
