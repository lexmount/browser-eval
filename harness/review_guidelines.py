"""Publish pinned shared guidance through a complete repository review entry."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

REPOSITORY = "lexmount/ai-template-py"
GUIDE = "docs/references/review-guidelines.md"
SUPPLEMENT = ".opencodereview/review-rules.md"
ENTRY = ".opencodereview/review-rules.generated.md"
LOCK = "review-guidelines.lock.json"


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
    ).stdout


def repository_root(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve()


def has_source_remote(root: Path) -> bool:
    for name in git(root, "remote").decode().splitlines():
        url = git(root, "remote", "get-url", name).decode().strip().rstrip("/").lower()
        if url.startswith("git@github.com:"):
            repository = url.removeprefix("git@github.com:")
        else:
            parsed = urlsplit(url)
            if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
                continue
            repository = parsed.path.lstrip("/")
        if repository.removesuffix(".git") == REPOSITORY:
            return True
    return False


def managed_path(root: Path, relative: str) -> Path:
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts:
        raise ValueError("Review files must remain inside the repository")
    path = root / name
    if path.exists() and not path.is_file():
        raise ValueError(f"Managed destination must be a file: {relative}")
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError(f"Managed paths must not be symlinks: {relative}")
        if candidate != path and candidate.exists() and not candidate.is_dir():
            raise ValueError(f"Managed parent must be a directory: {relative}")
    return path


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_guide(source: Path, revision: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Use a full 40-character source commit, not a moving ref")
    if not has_source_remote(source):
        raise ValueError(f"Configure a source checkout remote for {REPOSITORY}")
    if git(source, "cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("The source revision must identify a commit")
    content = git(source, "show", f"{revision}:{GUIDE}")
    if not content.strip():
        raise ValueError("The shared guide must not be empty")
    return content


def compose(guide: bytes, supplement: bytes) -> bytes:
    if not supplement.strip():
        raise ValueError("The repository review supplement must not be empty")
    header = f"<!-- Generated from {GUIDE} and {SUPPLEMENT}; edit those inputs. -->\n\n"
    return header.encode() + guide + b"\n\n---\n\n" + supplement


def check_routing(root: Path, guide: bytes, supplement: bytes, entry: bytes) -> None:
    config = json.loads(managed_path(root, ".opencodereview/rule.json").read_text())
    rules = config.get("rules") if isinstance(config, dict) else None
    if not isinstance(rules, list):
        raise ValueError("OCR rules must be a list")
    for rule in rules:
        if (isinstance(rule, dict) and rule.get("rule", "") == ""
                and rule.get("merge_system_rule", False) is False):
            continue
        if not isinstance(rule, dict) or not isinstance(rule.get("path"), str):
            raise ValueError("Each OCR rule must declare a path")
        if rule.get("merge_system_rule") is not True:
            raise ValueError("Every reachable OCR route must retain built-in rules")
        rule_path = rule.get("rule")
        if not isinstance(rule_path, str):
            raise ValueError("Each OCR rule must name its complete review entry")
        is_file = (
            "\n" not in rule_path and " " not in rule_path
            and Path(rule_path).suffix.lower() in {".md", ".txt", ".markdown"}
        )
        if is_file:
            body = entry if rule_path == ENTRY else managed_path(root, rule_path).read_bytes()
        else:
            body = rule_path.encode()
        if guide not in body or supplement not in body:
            raise ValueError("Every reachable OCR route must include shared and local guidance")
        # OCR uses the first matching rule; a catch-all makes later entries unreachable.
        if rule["path"] == "**":
            return
    raise ValueError("OCR needs a catch-all route for the complete review entry")


def pinned_guide(root: Path, source: Path | None = None) -> bytes:
    lock = json.loads(managed_path(root, LOCK).read_text())
    if not isinstance(lock, dict) or lock.get("version") != 2 or lock.get("repository") != REPOSITORY:
        raise ValueError("Unsupported review source lock")
    revision = lock.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("The source lock must pin a full commit")
    if not isinstance(lock.get("files"), dict) or set(lock["files"]) != {GUIDE}:
        raise ValueError("The source lock must cover exactly the shared guide")
    guide = managed_path(root, GUIDE).read_bytes()
    if not guide.strip() or digest(guide) != lock["files"][GUIDE]:
        raise ValueError("Local drift in the shared guide; synchronize from the source")
    if source is not None and guide != source_guide(source, revision):
        raise ValueError(f"Source mismatch at {revision}: {GUIDE}")
    return guide


def check(root: Path, source: Path | None = None) -> None:
    guide = pinned_guide(root, source)
    supplement = managed_path(root, SUPPLEMENT).read_bytes()
    entry = compose(guide, supplement)
    if managed_path(root, ENTRY).read_bytes() != entry:
        raise ValueError("Review entry is stale; run scripts/review_guidelines.py render")
    check_routing(root, guide, supplement, entry)
    print(f"Review guidance verified: {root}")


def install(root: Path, payloads: dict[str, bytes]) -> None:
    with TemporaryDirectory(prefix=".review-guidelines-", dir=root) as directory:
        staged = Path(directory)
        for path, content in payloads.items():
            (staged / Path(path).name).write_bytes(content)
        # Each replacement is atomic; the source lock is published last.
        for path in payloads:
            destination = managed_path(root, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            (staged / Path(path).name).replace(destination)


def render(root: Path) -> None:
    guide = pinned_guide(root)
    supplement = managed_path(root, SUPPLEMENT).read_bytes()
    entry = compose(guide, supplement)
    check_routing(root, guide, supplement, entry)
    install(root, {ENTRY: entry})
    check(root)


def sync(source: Path, revision: str, targets: list[Path]) -> None:
    guide = source_guide(source, revision)
    lock = {
        "version": 2, "repository": REPOSITORY, "revision": revision,
        "files": {GUIDE: digest(guide)},
    }
    prepared = []
    for root in targets:
        if root == source:
            raise ValueError("Edit the canonical source directly; do not sync over it")
        supplement = managed_path(root, SUPPLEMENT).read_bytes()
        entry = compose(guide, supplement)
        payloads = {GUIDE: guide, ENTRY: entry, LOCK: (json.dumps(lock, indent=2) + "\n").encode()}
        for path in payloads:
            managed_path(root, path)
        check_routing(root, guide, supplement, entry)
        prepared.append((root, payloads))
    for root, payloads in prepared:
        install(root, payloads)
        check(root, source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("check", help="Check the pinned guide and complete OCR entry")
    verify.add_argument("--source", type=Path, help="Also compare the pinned source commit")
    commands.add_parser("render", help="Regenerate the OCR entry after editing local requirements")
    update = commands.add_parser("sync", help="Update guidance in opted-in repository checkouts")
    update.add_argument("--source", type=Path, required=True)
    update.add_argument("--ref", required=True, help="Full merged source commit")
    update.add_argument("targets", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "sync":
            sync(repository_root(args.source), args.ref, [repository_root(p) for p in args.targets])
        else:
            root = repository_root(Path.cwd())
            if args.command == "render":
                render(root)
            else:
                check(root, repository_root(args.source) if args.source else None)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"Review guidance error: {exc}", file=sys.stderr)
        return 1
    return 0
