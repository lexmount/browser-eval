"""Resolve and import hash-bound reference inputs kept outside the source checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

from harness.reporting.assets import copy_bound_assets
from harness.web_fetch.safety import contained_path, safe_identifier
from harness.web_fetch.storage import data_home

MANIFEST_NAME = "source-inputs.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path, dataset: Path | None = None) -> dict:
    if path.is_symlink():
        raise ValueError("Reference input manifest must not be symlinked")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "frozen_reference_inputs"
    ):
        raise ValueError("Unsupported reference input manifest")
    safe_identifier(manifest["id"])
    declared_dataset = contained_path(
        path.parent, manifest["dataset"], reject_symlinks=True
    )
    selected_dataset = dataset or declared_dataset
    if (
        selected_dataset.resolve() != declared_dataset
        or digest(selected_dataset) != manifest["dataset_sha256"]
    ):
        raise ValueError("Reference inputs are bound to a different dataset")
    if not manifest.get("trees"):
        raise ValueError("Reference input trees are missing")
    for tree, files in manifest["trees"].items():
        safe_identifier(tree)
        if not files:
            raise ValueError("Reference input inventory must not be empty")
        for relative, expected in files.items():
            contained_path(path.parent, relative, reject_symlinks=True)
            if not isinstance(expected, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected
            ):
                raise ValueError("Reference input SHA-256 is invalid")
    return manifest


def input_root(manifest: dict) -> Path:
    return data_home() / "reference-inputs" / manifest["id"]


def verify_tree(root: Path, files: dict[str, str]) -> None:
    if root.is_symlink():
        raise ValueError("Reference input tree must not be symlinked")
    if not root.is_dir():
        if root.exists() or root.parent.exists():
            raise ValueError("Installed reference input tree is missing or is not a directory")
        raise FileNotFoundError(
            "Reference inputs are not installed; import the approved source copy with scripts/import_reference_inputs.py"
        )
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Reference inputs must not contain symlinks")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files):
        raise ValueError("Reference input inventory differs from its manifest")
    for relative, expected in files.items():
        path = contained_path(root, relative, reject_symlinks=True)
        if digest(path) != expected:
            raise ValueError("Reference input changed: " + relative)


def input_tree(dataset: Path, tree: str) -> Path:
    """Use the bound external tree, or the supplied dataset's own adjacent tree."""
    safe_identifier(tree)
    manifest_path = dataset.parent / MANIFEST_NAME
    if manifest_path.is_file() or manifest_path.is_symlink():
        manifest = load_manifest(manifest_path, dataset)
        if tree in manifest["trees"]:
            root = contained_path(input_root(manifest), tree, reject_symlinks=True)
            verify_tree(root, manifest["trees"][tree])
            return root
    return contained_path(dataset.parent, tree, reject_symlinks=True)


def require_mutable_inputs(dataset: Path) -> None:
    """Frozen external references can only be replaced by a new declared revision."""
    path = dataset.parent / MANIFEST_NAME
    if path.exists() or path.is_symlink():
        raise ValueError(
            "Pinned reference inputs are immutable; create a separate candidate dataset and sources"
        )


def check_reference_input_boundary(directory: Path) -> None:
    """Reject source-tree copies of inputs explicitly assigned to external storage."""
    for path in directory.rglob(MANIFEST_NAME):
        manifest = load_manifest(path)
        for tree in manifest["trees"]:
            local = contained_path(path.parent, tree, reject_symlinks=True)
            if local.exists():
                raise ValueError(
                    "External reference inputs duplicated in checkout: " + tree
                )


def import_inputs(manifest_path: Path, source: Path) -> Path:
    """Copy only declared input bytes; commit a new directory after verification."""
    manifest = load_manifest(manifest_path)
    output = input_root(manifest)
    if output.exists() or output.is_symlink():
        raise ValueError(
            "Reference input destination already exists; verify it or select a new manifest revision"
        )
    bindings = {
        f"{tree}/{name}": expected
        for tree, files in manifest["trees"].items()
        for name, expected in files.items()
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".reference-inputs-", dir=output.parent
    ) as work:
        staging = Path(work) / "inputs"
        copy_bound_assets(source, staging, bindings)
        for tree, files in manifest["trees"].items():
            verify_tree(staging / tree, files)
        staging.rename(output)
    return output


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--source", type=Path, help="Approved local source copy; no network download"
    )
    action.add_argument(
        "--check",
        action="store_true",
        help="Verify every installed input against the manifest",
    )
    args = parser.parse_args(argv)
    if args.check:
        manifest = load_manifest(args.manifest)
        root = input_root(manifest)
        for tree, files in manifest["trees"].items():
            verify_tree(root / tree, files)
    else:
        import_inputs(args.manifest, args.source)
    print("Reference input inventory and hashes verified")


if __name__ == "__main__":
    main()
