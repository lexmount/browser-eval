"""Fail closed unless WebArena-Verified comes from the pinned Lexmount fork."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

WEBARENA_VERIFIED_DISTRIBUTION = "webarena-verified"
WEBARENA_VERIFIED_MODULE = "webarena_verified"
WEBARENA_VERIFIED_FORK_URL = "https://github.com/lexmount/webarena-verified.git"
WEBARENA_VERIFIED_REVISION = "1925befb545c6433f7a7a5a77c473ce9e74eb468"


@dataclass(frozen=True)
class WebArenaRuntimeSourceIdentity:
    repository_url: str
    revision: str
    module_root: Path
    distribution_root: Path


def _canonical_repository_url(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("WebArena direct_url repository URL is missing")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("WebArena direct_url repository URL is invalid")
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not parsed.hostname or not path:
        raise RuntimeError("WebArena direct_url repository URL is invalid")
    return f"https://{parsed.hostname.lower()}{path.lower()}"


def verify_webarena_runtime_source(
    *,
    expected_repository: str = WEBARENA_VERIFIED_FORK_URL,
    expected_revision: str = WEBARENA_VERIFIED_REVISION,
) -> WebArenaRuntimeSourceIdentity:
    """Verify VCS provenance and the import path before WebArena work begins."""

    try:
        distribution = importlib.metadata.distribution(WEBARENA_VERIFIED_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("the pinned WebArena-Verified distribution is not installed") from exc

    raw_direct_url = distribution.read_text("direct_url.json")
    try:
        direct_url = json.loads(raw_direct_url) if raw_direct_url is not None else None
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("WebArena direct_url metadata is malformed") from exc
    if not isinstance(direct_url, dict):
        raise RuntimeError("WebArena direct_url metadata is missing")
    vcs = direct_url.get("vcs_info")
    if not isinstance(vcs, dict) or vcs.get("vcs") != "git":
        raise RuntimeError("WebArena direct_url metadata is not a Git checkout")
    revision = vcs.get("commit_id")
    requested_revision = vcs.get("requested_revision")
    if (
        not isinstance(revision, str)
        or not isinstance(requested_revision, str)
        or revision != expected_revision
        or requested_revision != expected_revision
    ):
        raise RuntimeError("WebArena runtime revision differs from the pinned fork revision")
    repository_url = direct_url.get("url")
    if _canonical_repository_url(repository_url) != _canonical_repository_url(expected_repository):
        raise RuntimeError("WebArena runtime repository is not the Lexmount fork")

    files = distribution.files
    package_init = next(
        (
            entry
            for entry in files or ()
            if str(entry).replace("\\", "/") == f"{WEBARENA_VERIFIED_MODULE}/__init__.py"
        ),
        None,
    )
    if package_init is None:
        raise RuntimeError("WebArena distribution does not own its import package")
    distribution_root = Path(distribution.locate_file("")).resolve()
    expected_init = Path(distribution.locate_file(package_init)).resolve()
    try:
        expected_init.relative_to(distribution_root)
    except ValueError as exc:
        raise RuntimeError("WebArena distribution package path escapes its installation") from exc
    if not expected_init.is_file():
        raise RuntimeError("WebArena distribution package entry point is missing")
    expected_module_root = expected_init.parent
    try:
        spec = importlib.util.find_spec(WEBARENA_VERIFIED_MODULE)
    except (AttributeError, ImportError, ValueError) as exc:
        raise RuntimeError("WebArena import metadata is malformed") from exc
    if spec is None or spec.origin is None:
        raise RuntimeError("WebArena import package is missing")
    module_origin = Path(spec.origin).resolve()
    search_locations = tuple(
        Path(value).resolve() for value in (spec.submodule_search_locations or ())
    )
    if module_origin != expected_init or search_locations != (expected_module_root,):
        raise RuntimeError("WebArena import path does not belong to the pinned distribution")
    return WebArenaRuntimeSourceIdentity(
        repository_url=str(repository_url),
        revision=revision,
        module_root=expected_module_root,
        distribution_root=distribution_root,
    )
