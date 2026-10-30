"""Route-hash snapshots that make every rendered report change visible in review.

Each snapshot maps the routes of one synthetic report build to their SHA-256.
Paths of the temporary build directory are replaced with a fixed token first,
so the digests depend only on rendered content.

Set ``UPDATE_GOLDEN=1`` to rewrite the snapshots after an intended change.
Set ``GOLDEN_KEEP=<directory>`` to keep the normalized builds; building two
revisions this way lets ``diff -r`` show the exact changed lines.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"
BUILD_ROOT_TOKEN = b"<build-root>"
PATH_INPUT_TOKEN = b"<path-bearing-input-sha256>"
SOURCE_DIGEST_TOKEN = b"<source-sha256>"
def normalized_routes(
    output: Path,
    build_root: Path,
    path_inputs: Iterable[Path] = (),
    source_digests: Iterable[str] = (),
) -> dict[str, bytes]:
    """Return every file under ``output`` with machine-specific bytes replaced by tokens.

    ``path_inputs`` are evidence files whose content names absolute paths under
    ``build_root``. Reports that bind them by SHA-256 would otherwise differ per
    machine, so their digests are replaced as well. ``source_digests`` are hashes
    of repository source code recorded in the evidence; they change with every
    commit.
    """
    replacements = [(str(build_root.resolve()).encode(), BUILD_ROOT_TOKEN),
                    (str(build_root).encode(), BUILD_ROOT_TOKEN)]
    replacements += [(hashlib.sha256(path.read_bytes()).hexdigest().encode(), PATH_INPUT_TOKEN)
                     for path in path_inputs]
    replacements += [(digest.encode(), SOURCE_DIGEST_TOKEN) for digest in source_digests]
    routes = {}
    for path in sorted(output.rglob("*")):
        if path.is_file():
            content = path.read_bytes()
            for old, new in replacements:
                content = content.replace(old, new)
            routes[path.relative_to(output).as_posix()] = content
    return routes


def check_golden(
    name: str,
    output: Path,
    build_root: Path,
    *,
    path_inputs: Iterable[Path] = (),
    source_digests: Iterable[str] = (),
) -> None:
    """Compare the route digests of ``output`` with ``tests/golden/<name>.json``."""
    routes = normalized_routes(output, build_root, path_inputs, source_digests)
    if not routes:
        pytest.fail(f"{name}: the build produced no routes")
    actual = {route: hashlib.sha256(content).hexdigest() for route, content in routes.items()}
    keep = os.environ.get("GOLDEN_KEEP")
    if keep:
        for route, content in routes.items():
            target = Path(keep) / name / route
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    if not path.is_file():
        pytest.fail(f"{name}: no snapshot; run with UPDATE_GOLDEN=1 to create {path}")
    expected = json.loads(path.read_text(encoding="utf-8"))
    if actual == expected:
        return
    lines = [f"{name}: rendered output differs from {path.relative_to(GOLDEN_DIR.parent.parent)}"]
    for label, routes_ in (
        ("changed", sorted(r for r in actual.keys() & expected.keys() if actual[r] != expected[r])),
        ("added", sorted(actual.keys() - expected.keys())),
        ("removed", sorted(expected.keys() - actual.keys())),
    ):
        lines.extend(f"  {label}: {route}" for route in routes_)
    lines.append(
        "If the change is intended, rerun with UPDATE_GOLDEN=1 and commit the snapshot. "
        "GOLDEN_KEEP=<dir> keeps the normalized build for diff -r against another revision."
    )
    pytest.fail("\n".join(lines))
