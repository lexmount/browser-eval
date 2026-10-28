"""Download fixed, hash-verified public datasets; cached snapshots work offline."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen


def binding(track: str, revision: str | None = None) -> dict:
    lock = json.loads(Path(__file__).with_name("data-lock.json").read_bytes())
    current = lock["datasets"][track]
    choices = [current, *current.get("history", [])]
    selected = next((item for item in choices if revision is None or item["revision"] == revision), None)
    if selected is None:
        raise ValueError("Dataset revision is not recorded in data-lock.json")
    if not re.fullmatch(r"[0-9a-f]{40}", selected["revision"]):
        raise ValueError("Dataset revision must be a full immutable commit")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", selected["repository"]):
        raise ValueError("Invalid Hugging Face repository")
    return selected


def cache_home() -> Path:
    root = os.environ.get("BROWSER_EVAL_DATASET_CACHE")
    return (Path(root).expanduser() if root else Path.home() / ".browser-eval/datasets").resolve()


def dependencies(track: str) -> dict[str, dict[str, str]]:
    """Record installed dataset commits, including explicitly composed sources."""
    pending, result = [track], {}
    while pending:
        name = pending.pop()
        if name in result:
            continue
        selected = binding(name)
        result[name] = {key: selected[key] for key in ("repository", "revision")}
        pending.extend(selected.get("dataset_sources", {}).values())
    return result


def safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Unsafe dataset path")
    target = root / path
    if root.is_symlink() or any((root / Path(*path.parts[:index])).is_symlink()
                                for index in range(1, len(path.parts) + 1)):
        raise ValueError("Dataset paths cannot contain symlinks")
    return target


def verify_file(path: Path, expected: dict) -> None:
    if (not path.is_file() or path.stat().st_size != expected["size"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]):
        raise ValueError("Dataset file hash or size mismatch: " + path.name)


def unpack(raw: bytes, destination: Path, selected: dict) -> None:
    expected = selected["files"]
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            path = safe_path(destination, member.name)
            if (not member.isfile() or member.name not in expected or member.name in seen
                    or member.size != expected[member.name]["size"]):
                raise ValueError("Dataset archive inventory mismatch")
            seen.add(member.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output)
            verify_file(path, expected[member.name])
    if seen != set(expected):
        raise ValueError("Dataset archive is incomplete")


@lru_cache(maxsize=16)
def _resolve(track: str, revision: str, home: str) -> Path:
    selected = binding(track, revision)
    root = Path(home) / track / revision
    if root.exists() or root.is_symlink():
        actual = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Dataset cache cannot contain symlinks")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
        if actual != set(selected["files"]):
            raise ValueError("Dataset cache inventory differs from its locked snapshot")
        for relative, expected in selected["files"].items():
            verify_file(safe_path(root, relative), expected)
        return root
    if os.environ.get("HF_HUB_OFFLINE", "").lower() in {"1", "true", "yes"}:
        raise FileNotFoundError("Pinned dataset is not cached; run python -m harness.benchmarks.data before going offline")
    root.parent.mkdir(parents=True, exist_ok=True)
    archive = selected["archive"]
    url = f"https://huggingface.co/datasets/{selected['repository']}/resolve/{revision}/{archive['path']}"
    with urlopen(url, timeout=60) as response:
        raw = response.read(archive["size"] + 1)
    if len(raw) != archive["size"] or hashlib.sha256(raw).hexdigest() != archive["sha256"]:
        raise ValueError("Downloaded dataset archive hash or size mismatch")
    with tempfile.TemporaryDirectory(dir=root.parent, prefix=".download-") as directory:
        candidate = Path(directory) / "snapshot"
        candidate.mkdir()
        unpack(raw, candidate, selected)
        try:
            candidate.rename(root)
        except OSError:
            if not root.is_dir():
                raise
            for relative, expected in selected["files"].items():
                verify_file(safe_path(root, relative), expected)
    return root


def snapshot(track: str, revision: str | None = None) -> Path:
    selected = binding(track, revision)
    return _resolve(track, selected["revision"], str(cache_home()))


def definition_path(track: str, relative: str, revision: str | None = None) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Definition path must be relative and cannot traverse parents")
    selected = binding(track, revision)
    relative = Path(relative).as_posix()
    name = "definitions" if relative == "." else "definitions/" + relative
    members = {key: value for key, value in selected["files"].items() if key.startswith(name + "/")}
    if name not in selected["files"] and not members:
        raise FileNotFoundError("Unknown benchmark definition: " + relative)
    path = safe_path(snapshot(track, revision), name)
    if members:
        if not path.is_dir():
            raise ValueError("Dataset definition directory is missing")
        for key, value in members.items():
            verify_file(safe_path(snapshot(track, revision), key), value)
    else:
        verify_file(path, selected["files"][name])
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    tracks = json.loads(Path(__file__).with_name("data-lock.json").read_bytes())["datasets"]
    parser.add_argument("tracks", nargs="*", metavar="TRACK", help="Defaults to every registered dataset")
    args = parser.parse_args()
    prepared = set()
    for track in args.tracks or tracks:
        if track not in tracks:
            parser.error("Unknown track: " + track)
        for source in dependencies(track):
            if source not in prepared:
                snapshot(source)
                print(source + ": " + binding(source)["revision"])
                prepared.add(source)


if __name__ == "__main__":
    main()
