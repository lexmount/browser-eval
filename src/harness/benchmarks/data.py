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
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files as package_files
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from uuid import uuid4


@lru_cache(maxsize=2)
def _read_lock(path: str, stamp: tuple[int, int, int]) -> dict:
    return json.loads(Path(path).read_bytes())


def _lock() -> dict:
    path = Path(__file__).with_name("data-lock.json")
    status = path.stat()
    return _read_lock(str(path), (status.st_ino, status.st_mtime_ns, status.st_size))


def binding(track: str, revision: str | None = None) -> dict:
    lock = _lock()
    current = lock["datasets"][track]
    choices = [current, *current.get("history", [])]
    selected = next((item for item in choices if revision is None or item["revision"] == revision), None)
    if selected is None:
        raise ValueError("Dataset revision is not recorded in data-lock.json")
    if not re.fullmatch(r"[0-9a-f]{40}", selected["revision"]):
        raise ValueError("Dataset revision must be a full immutable commit")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", selected["repository"]):
        raise ValueError("Invalid Hugging Face repository")
    return deepcopy(selected)


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


def canonical_repository(repository: str) -> str:
    """Recognize recorded Hub moves without changing frozen commit identities."""
    metadata = json.loads(package_files(__package__).joinpath("cards/metadata.json").read_bytes())
    return next((item["repo_id"] for item in metadata.values()
                 if repository == item["repo_id"] or repository in item.get("previous_repo_ids", [])),
                repository)


def _definition_bindings(selected: dict, *, composed: bool = True,
                         selections: dict | None = None) -> dict:
    """Compare recorded inputs without substituting today's provider edition."""
    # This ledger records corrections; no collection or scorer executes it.
    def resources(item):
        return {name: value for name, value in item["files"].items()
                if name.startswith("definitions/")
                and name != "definitions/scoring-revisions.json"}
    result = resources(selected)
    if not composed:
        return result
    for identity, source in selected.get("dataset_sources", {}).items():
        target = selected["datasets"][identity]
        name = "definitions/" + target["path"]
        recorded = selected.get("external_sources", {}).get(source)
        supplied = selections.get(source) if selections is not None else None
        if recorded is None and supplied is None:
            # Self-contained historical editions retain their own actual bytes.
            if name not in result or result[name]["sha256"] != target["sha256"]:
                raise ValueError("Composed benchmark dataset requires recorded source context")
            continue
        provider = recorded if recorded is not None else supplied
        if (not isinstance(provider, dict)
                or not isinstance(provider.get("repository"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", provider.get("revision", ""))
                or provider.get("datasets", {}).get(identity) != target):
            raise ValueError("Composed benchmark dataset identity differs")
        if recorded is not None and supplied is not None:
            if (canonical_repository(recorded["repository"]) != canonical_repository(supplied.get("repository"))
                    or recorded["revision"] != supplied.get("revision")
                    or supplied.get("datasets", {}).get(identity) != target
                    or any(supplied.get("files", {}).get(key) != value
                           for key, value in recorded.get("files", {}).items())):
                raise ValueError("Composed benchmark source differs from recorded context")
        additions = resources(provider)
        if name not in additions or additions[name]["sha256"] != target["sha256"]:
            raise ValueError("Composed benchmark dataset source file binding differs")
        for key, value in additions.items():
            if key == "definitions/registry.json":
                continue
            if key in result and result[key] != value:
                raise ValueError("Composed benchmark source conflicts with recorded bytes")
            result[key] = value
    return result


def legacy_dependencies(track: str, source_revision: str) -> dict[str, dict[str, str]]:
    """Upgrade a known Git-pinned collection only when its inputs are unchanged."""
    selected = binding(track)
    choices = [selected, *selected.get("history", [])]
    # The first published input edition for a Git pin binds that original source.
    # Later HF corrections sharing its provenance must not bless changed inputs.
    authority = selected.get("source_editions")
    if authority is None:
        original = next((item for item in reversed(choices)
                         if item.get("source_revision") == source_revision), None)
    else:
        original = next((item for item in choices
                         if item["revision"] == authority.get(source_revision)
                         and item.get("source_revision") == source_revision), None)
    # The historical Git package read its bundled definitions. Applying today's
    # composition to that side would replace the very old bytes we must compare.
    if (original is None
            or _definition_bindings(original, composed=False) != _definition_bindings(
                selected, selections={source: binding(source)
                                      for source in selected.get("dataset_sources", {}).values()})):
        raise ValueError("Legacy benchmark dataset source is unknown or its inputs changed")
    return dependencies(track)


class _TokenRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        before, after = urlsplit(request.full_url), urlsplit(new_url)
        if redirected is not None and (before.scheme, before.netloc) != (after.scheme, after.netloc):
            redirected.remove_header("Authorization")
        return redirected


def read_hf_file(repository: str, revision: str, relative: str, maximum: int) -> bytes:
    """Use the configured Hub endpoint with optional explicit token authentication."""
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("HF_ENDPOINT must be an HTTP(S) base URL")
    token = os.environ.get("HF_TOKEN", "").strip()
    url = f"{endpoint}/datasets/{repository}/resolve/{revision}/{relative}"
    request = Request(url, headers={"Authorization": "Bearer " + token}) if token else url
    open_file = build_opener(_TokenRedirects()).open if token else urlopen
    with open_file(request, timeout=60) as response:
        raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Dataset file exceeds its bounded download size")
    return raw


def fetch_archive(repository: str, revision: str, archive: dict) -> bytes:
    """Read and verify the same immutable archive for pinning and installation."""
    if (archive.get("path") != "definitions.tar.gz"
            or type(archive.get("size")) is not int or archive["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", archive.get("sha256", ""))):
        raise ValueError("Invalid dataset archive binding")
    raw = read_hf_file(repository, revision, archive["path"], archive["size"])
    if len(raw) != archive["size"] or hashlib.sha256(raw).hexdigest() != archive["sha256"]:
        raise ValueError("Dataset archive hash or size mismatch")
    return raw


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
        _verify_snapshot(root, selected)
        return root
    if os.environ.get("HF_HUB_OFFLINE", "").lower() in {"1", "true", "yes"}:
        raise FileNotFoundError("Pinned dataset is not cached; run python -m harness.benchmarks.data before going offline")
    root.parent.mkdir(parents=True, exist_ok=True)
    raw = fetch_archive(selected["repository"], revision, selected["archive"])
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


def _verify_snapshot(root: Path, selected: dict) -> None:
    if root.is_symlink():
        raise ValueError("Dataset cache cannot contain symlinks")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Dataset cache cannot contain symlinks")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    expected = set(selected["files"])
    if actual != expected:
        raise ValueError("Dataset cache inventory differs from its locked snapshot; "
                         f"extra={sorted(actual - expected)}, missing={sorted(expected - actual)}. "
                         "Run python -m harness.benchmarks.data --repair <track> while online")
    for relative, specification in selected["files"].items():
        verify_file(safe_path(root, relative), specification)


def repair(track: str, revision: str | None = None) -> Path:
    """Preserve an invalid snapshot before restoring its immutable archive."""
    selected = binding(track, revision)
    root = cache_home() / track / selected["revision"]
    if root.exists() or root.is_symlink():
        try:
            _verify_snapshot(root, selected)
        except ValueError:
            if os.environ.get("HF_HUB_OFFLINE", "").lower() in {"1", "true", "yes"}:
                raise ValueError("Dataset repair requires online access; unset HF_HUB_OFFLINE "
                                 f"and run python -m harness.benchmarks.data --repair {track}") from None
            # The sibling is retained even if the subsequent download fails.
            root.rename(root.with_name(root.name + ".invalid-" + uuid4().hex))
    _resolve.cache_clear()
    return snapshot(track, selected["revision"])


def snapshot(track: str, revision: str | None = None) -> Path:
    """Return a fully verified snapshot, including on warm in-process lookups."""
    selected = binding(track, revision)
    root = _resolve(track, selected["revision"], str(cache_home()))
    _verify_snapshot(root, selected)
    return root


def definition_path(track: str, relative: str, revision: str | None = None) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Definition path must be relative and cannot traverse parents")
    selected = binding(track, revision)
    relative = Path(relative).as_posix()
    name = "definitions" if relative == "." else "definitions/" + relative
    members = {key: value for key, value in selected["files"].items() if key.startswith(name + "/")}
    if name not in selected["files"] and not members:
        raise FileNotFoundError("Unknown benchmark definition: " + relative)
    # Resolution is cached; this boundary verifies only the requested members.
    root = _resolve(track, selected["revision"], str(cache_home()))
    path = safe_path(root, name)
    if members:
        if not path.is_dir():
            raise ValueError("Dataset definition directory is missing")
        _verify_snapshot(path, {"files": {
            key.removeprefix(name + "/"): value for key, value in members.items()
        }})
    else:
        verify_file(path, selected["files"][name])
    return path


def resolve_definition_path(track: str, path: Path) -> Path:
    """Reverify explicit installed-cache paths; preserve caller-owned local inputs."""
    path = Path(path)
    home = cache_home() / track
    absolute, physical = path.absolute(), path.resolve()
    if absolute != physical:
        for component in (absolute, *absolute.parents):
            if component.is_symlink() and (
                component.resolve().is_relative_to(home)
                or component.parent.resolve().is_relative_to(home)
            ):
                raise ValueError("Dataset definition paths cannot contain symlinks")
    try:
        relative = physical.relative_to(home)
    except ValueError:
        return path
    if len(relative.parts) >= 2 and relative.parts[1] == "definitions":
        return definition_path(track, Path(*relative.parts[2:]).as_posix(),
                               revision=relative.parts[0])
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    tracks = _lock()["datasets"]
    parser.add_argument("--repair", action="store_true", help="Quarantine invalid caches and restore locked inputs online")
    parser.add_argument("tracks", nargs="*", metavar="TRACK", help="Defaults to every registered dataset")
    args = parser.parse_args()
    prepared = set()
    for track in args.tracks or tracks:
        if track not in tracks:
            parser.error("Unknown track: " + track)
        for source in dependencies(track):
            if source not in prepared:
                (repair if args.repair else snapshot)(source)
                print(source + ": " + binding(source)["revision"])
                prepared.add(source)


if __name__ == "__main__":
    main()
