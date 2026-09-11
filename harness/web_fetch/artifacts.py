"""Hash-bound private provider artifacts.

Raw API envelopes are useful audit evidence, but may contain account-specific or
sensitive fields.  They stay in the persistent evidence tree and are verified at
every aggregation/publication boundary; they are never projected into a report.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Iterable

from .safety import contained_path


PRIVATE_ARTIFACT_ROOT = "private_artifacts"


def raw_response_binding(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Return a complete raw-response binding, rejecting partial claims."""
    values = {
        "path": metadata.get("raw_response_path"),
        "sha256": metadata.get("raw_response_sha256"),
        "bytes": metadata.get("raw_response_bytes"),
    }
    present = {key for key, value in values.items() if value is not None}
    if not present:
        return None
    if present != set(values):
        raise ValueError("Raw response evidence binding is incomplete")
    path = Path(str(values["path"]))
    if not path.parts or path.parts[0] != PRIVATE_ARTIFACT_ROOT:
        raise ValueError("Raw response evidence must stay under private_artifacts")
    digest = values["sha256"]
    size = values["bytes"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)):
        raise ValueError("Raw response evidence has an invalid SHA-256 binding")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("Raw response evidence has an invalid byte-size binding")
    return values


def record_private_bindings(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect final and retained-retry private artifacts from one result."""
    bindings: list[dict[str, Any]] = []
    final = raw_response_binding(record.get("provider_metadata", {}))
    if final:
        bindings.append(final)
    for attempt in record.get("provider_metadata", {}).get("retry", {}).get("attempts", []):
        private = attempt.get("private_artifacts", [])
        if not isinstance(private, list):
            raise ValueError("Retry private_artifacts must be a list")
        for binding in private:
            if not isinstance(binding, dict):
                raise ValueError("Retry private artifact binding must be an object")
            normalized = raw_response_binding({
                "raw_response_path": binding.get("path"),
                "raw_response_sha256": binding.get("sha256"),
                "raw_response_bytes": binding.get("bytes"),
            })
            if normalized:
                bindings.append(normalized)
    seen: dict[str, tuple[str, int]] = {}
    for binding in bindings:
        identity = (binding["sha256"], binding["bytes"])
        if binding["path"] in seen and seen[binding["path"]] != identity:
            raise ValueError("Private artifact has conflicting bindings: " + binding["path"])
        seen[binding["path"]] = identity
    return [dict(path=path, sha256=digest, bytes=size)
            for path, (digest, size) in seen.items()]


def verify_private_binding(root: Path, binding: dict[str, Any]) -> Path:
    """Verify a private artifact without returning or decoding its contents."""
    normalized = raw_response_binding({
        "raw_response_path": binding.get("path"),
        "raw_response_sha256": binding.get("sha256"),
        "raw_response_bytes": binding.get("bytes"),
    })
    if normalized is None:  # pragma: no cover - all-empty is invalid for a caller
        raise ValueError("Private artifact binding is empty")
    # ``contained_path`` resolves links to enforce containment. Check the
    # lexical chain as well so an in-tree link cannot masquerade as evidence.
    lexical = root.resolve()
    for part in Path(normalized["path"]).parts:
        lexical = lexical / part
        if lexical.is_symlink():
            raise ValueError("Private artifact is missing or unsafe: " + normalized["path"])
    path = contained_path(root, normalized["path"])
    if not path.is_file():
        raise ValueError("Private artifact is missing or unsafe: " + normalized["path"])
    stat = path.stat()
    if stat.st_size != normalized["bytes"]:
        raise ValueError("Private artifact byte size changed: " + normalized["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != normalized["sha256"]:
        raise ValueError("Private artifact hash changed: " + normalized["path"])
    return path


def verify_record_private_artifacts(root: Path, record: dict[str, Any]) -> None:
    if "_raw_response_b64" in record.get("provider_metadata", {}):
        raise ValueError("Encoded raw response leaked into persisted result metadata")
    for binding in record_private_bindings(record):
        verify_private_binding(root, binding)


def materialize_private_artifacts(
    destination: Path, source: Path, records: Iterable[dict[str, Any]]
) -> None:
    """Copy only verified private bindings between persistent evidence trees."""
    for record in records:
        for binding in record_private_bindings(record):
            origin = verify_private_binding(source, binding)
            target = contained_path(destination, binding["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                verify_private_binding(destination, binding)
            else:
                shutil.copy2(origin, target)
                verify_private_binding(destination, binding)
