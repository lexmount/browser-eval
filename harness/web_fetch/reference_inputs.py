"""Resolve and import hash-bound reference inputs kept outside the source checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import urlopen

from harness.reporting.assets import copy_bound_assets
from harness.reporting.packs import read_packed_file
from harness.web_fetch.safety import contained_path, reject_evidence_symlinks, safe_identifier
from harness.web_fetch.storage import data_home

MANIFEST_NAME = "source-inputs.json"
_PACKED_INPUT_PREFIXES = ("sources/", "list-references/")
_MAX_REMOTE_FILE_BYTES = 64_000_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path, dataset: Path | None = None) -> dict:
    if path.is_symlink():
        raise ValueError("Reference input manifest must not be symlinked")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") not in {1, 2}
        or manifest.get("kind") != "frozen_reference_inputs"
    ):
        raise ValueError("Unsupported reference input manifest")
    safe_identifier(manifest["id"])
    declared_dataset = contained_path(path.parent, manifest["dataset"], reject_symlinks=True)
    selected_dataset = dataset or declared_dataset
    if selected_dataset.resolve() != declared_dataset:
        raise ValueError("Reference inputs are bound to a different dataset")
    if manifest["schema_version"] == 1:
        if digest(selected_dataset) != manifest["dataset_sha256"]:
            raise ValueError("Reference inputs are bound to a different dataset")
    else:
        catalog = contained_path(path.parent, manifest["catalog"], reject_symlinks=True)
        if digest(catalog) != manifest["catalog_sha256"]:
            raise ValueError("Frozen dataset catalog changed")
        definition = json.loads(catalog.read_text(encoding="utf-8"))
        if (
            definition.get("kind") != "frozen_dataset_catalog"
            or definition.get("dataset_sha256") != manifest["dataset_sha256"]
            or manifest.get("files", {}).get(manifest["dataset"]) != manifest["dataset_sha256"]
        ):
            raise ValueError("Frozen dataset binding differs from its catalog")
        for relative, expected in manifest["files"].items():
            contained_path(path.parent, relative, reject_symlinks=True)
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Reference input SHA-256 is invalid")
    if not isinstance(manifest.get("trees"), dict) or (
        not manifest["trees"] and manifest["schema_version"] == 1
    ):
        raise ValueError("Reference input trees are missing")
    for tree, files in manifest["trees"].items():
        safe_identifier(tree)
        if not files:
            raise ValueError("Reference input inventory must not be empty")
        for relative, expected in files.items():
            contained_path(path.parent, relative, reject_symlinks=True)
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Reference input SHA-256 is invalid")
    return manifest


def input_root(manifest: dict) -> Path:
    return data_home() / "reference-inputs" / manifest["id"]


def verify_catalog(path: Path, manifest: dict, raw: bytes) -> None:
    """Bind the public task directory to the exact frozen task definitions."""
    catalog = json.loads((path.parent / manifest["catalog"]).read_bytes())
    dataset = json.loads(raw)
    cases = dataset.get("cases", [])
    if (
        catalog.get("case_count") != len(cases)
        or len(catalog.get("cases", [])) != len(cases)
        or any(catalog.get(key) != dataset.get(key) for key in ("id", "name", "coverage_plan"))
    ):
        raise ValueError("Frozen dataset catalog inventory differs from its dataset")
    for declared, case in zip(catalog["cases"], cases):
        definition = hashlib.sha256(
            json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if declared.get("definition_sha256") != definition:
            raise ValueError("Frozen task definition differs from its catalog")
        for key, value in declared.items():
            if key == "definition_sha256":
                continue
            actual = case.get(key)
            if key == "reference_sha256":
                text = case.get("ground_truth_main_content", "")
                actual = hashlib.sha256(text.encode()).hexdigest() if text else None
            if value != actual:
                raise ValueError("Frozen task catalog metadata differs from its dataset")


def read_dataset_bytes(dataset: Path) -> bytes:
    """Read a dataset from its explicit binding without changing its frozen bytes."""
    path = dataset.parent / MANIFEST_NAME
    if path.is_symlink():
        raise ValueError("Reference input manifest must not be symlinked")
    if path.is_file() or path.is_symlink():
        # A sibling manifest governs its declared dataset, not unrelated configs.
        declared = json.loads(path.read_text(encoding="utf-8")).get("dataset")
        if declared and dataset.resolve() == (path.parent / declared).resolve():
            manifest = load_manifest(path, dataset)
            if manifest["schema_version"] == 2:
                selected = contained_path(
                    input_root(manifest), manifest["dataset"], reject_symlinks=True
                )
                if not selected.exists():
                    if input_root(manifest).exists():
                        raise ValueError("Installed frozen dataset is missing")
                    raise FileNotFoundError(
                        "Reference inputs are not installed; import the approved source copy with scripts/import_reference_inputs.py"
                    )
                raw = selected.read_bytes()
                if hashlib.sha256(raw).hexdigest() != manifest["dataset_sha256"]:
                    raise ValueError("Frozen dataset changed")
                verify_catalog(path, manifest, raw)
                return raw
    return dataset.read_bytes()


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
        if manifest["schema_version"] == 2:
            read_dataset_bytes(dataset)
        if tree in manifest["trees"]:
            root = contained_path(input_root(manifest), tree, reject_symlinks=True)
            verify_tree(root, manifest["trees"][tree])
            return root
        if manifest["schema_version"] == 2:
            root = contained_path(input_root(manifest), tree, reject_symlinks=True)
            local = contained_path(dataset.parent, tree, reject_symlinks=True)
            if root.exists() or local.exists():
                raise ValueError("Undeclared reference input tree: " + tree)
            return root
    root = contained_path(dataset.parent, tree, reject_symlinks=True)
    reject_evidence_symlinks(root)
    return root


def require_mutable_inputs(dataset: Path) -> None:
    """Frozen external references can only be replaced by a new declared revision."""
    path = dataset.parent / MANIFEST_NAME
    if (
        path.exists()
        or path.is_symlink()
        or dataset.resolve().is_relative_to(data_home() / "reference-inputs")
    ):
        raise ValueError(
            "Pinned reference inputs are immutable; create a separate candidate dataset and sources"
        )


def check_reference_input_boundary(directory: Path) -> None:
    """Reject source-tree copies of inputs explicitly assigned to external storage."""
    for path in directory.rglob(MANIFEST_NAME):
        manifest = load_manifest(path)
        for relative in manifest.get("files", {}):
            if contained_path(path.parent, relative, reject_symlinks=True).exists():
                raise ValueError("External reference input duplicated in checkout: " + relative)
        trees = set(manifest["trees"])
        if manifest["schema_version"] == 2:
            trees.update({"sources", "list-references"})
        for tree in trees:
            local = contained_path(path.parent, tree, reject_symlinks=True)
            if local.exists():
                raise ValueError("External reference inputs duplicated in checkout: " + tree)


def import_inputs(manifest_path: Path, source: Path) -> Path:
    """Copy only declared input bytes; commit a new directory after verification."""
    manifest = load_manifest(manifest_path)
    output = input_root(manifest)
    if output.exists() or output.is_symlink():
        raise ValueError(
            "Reference input destination already exists; verify it or select a new manifest revision"
        )
    bindings = {
        **manifest.get("files", {}),
        **{
            f"{tree}/{name}": expected
            for tree, files in manifest["trees"].items()
            for name, expected in files.items()
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reference-inputs-", dir=output.parent) as work:
        staging = Path(work) / "inputs"
        copy_bound_assets(source, staging, bindings)
        for tree, files in manifest["trees"].items():
            verify_tree(staging / tree, files)
        if manifest["schema_version"] == 2:
            verify_catalog(manifest_path, manifest, (staging / manifest["dataset"]).read_bytes())
        staging.rename(output)
    return output


def _remote_file_url(manifest_url: str, relative: str) -> str:
    parts = urlsplit(manifest_url)
    if parts.scheme != "https" or not parts.netloc or not parts.path.endswith("/manifest.json"):
        raise ValueError("Packed snapshot manifest must use an HTTPS manifest.json URL")
    selected = Path(relative)
    if selected.is_absolute() or not selected.parts or ".." in selected.parts:
        raise ValueError("Packed snapshot file path escapes its origin")
    path = parts.path[: -len("manifest.json")] + quote(relative, safe="/")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _download_bytes(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        declared = response.headers.get("Content-Length")
        if declared is not None and int(declared) > _MAX_REMOTE_FILE_BYTES:
            raise ValueError("Packed snapshot file exceeds the download limit")
        raw = response.read(_MAX_REMOTE_FILE_BYTES + 1)
    if len(raw) > _MAX_REMOTE_FILE_BYTES:
        raise ValueError("Packed snapshot file exceeds the download limit")
    return raw


def import_packed_snapshot(
    manifest_url: str,
    expected_manifest_sha256: str,
    expected_dataset_sha256: str,
    identifier: str,
    *,
    fetch_bytes=_download_bytes,
) -> Path:
    """Restore only benchmark inputs from one immutable public packed snapshot."""
    safe_identifier(identifier)
    for label, value in (
        ("manifest", expected_manifest_sha256),
        ("dataset", expected_dataset_sha256),
    ):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"Packed snapshot {label} SHA-256 is invalid")
    manifest_raw = fetch_bytes(manifest_url)
    if hashlib.sha256(manifest_raw).hexdigest() != expected_manifest_sha256:
        raise ValueError("Packed snapshot manifest differs from its pinned hash")
    manifest = json.loads(manifest_raw)
    if manifest.get("schema_version") != 2 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported packed snapshot manifest")
    if any(not isinstance(name, str) for name in manifest["files"]):
        raise ValueError("Packed snapshot file name is invalid")
    selected = {
        name: entry
        for name, entry in manifest["files"].items()
        if name == "dataset.json" or name.startswith(_PACKED_INPUT_PREFIXES)
    }
    if "dataset.json" not in selected:
        raise ValueError("Packed snapshot does not contain dataset.json")
    if selected["dataset.json"].get("sha256") != expected_dataset_sha256:
        raise ValueError("Packed snapshot dataset differs from its pinned hash")
    source_dataset_sha256 = selected["dataset.json"].get("source_sha256")
    if (
        source_dataset_sha256 is not None
        and source_dataset_sha256 != expected_dataset_sha256
    ):
        raise ValueError(
            "Packed snapshot dataset is a publication projection, not the scored source dataset"
        )
    for name, entry in selected.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Packed snapshot input path escapes its destination")
        if not isinstance(entry, dict) or not isinstance(entry.get("parts"), list):
            raise ValueError("Packed snapshot file entry is invalid")

    class RemoteArchive:
        def __init__(self):
            self.downloaded = {"manifest.json": manifest_raw}

        def read(self, name):
            if name not in self.downloaded:
                self.downloaded[name] = fetch_bytes(_remote_file_url(manifest_url, name))
            return self.downloaded[name]

    output = data_home() / "reference-inputs" / identifier
    if output.exists() or output.is_symlink():
        raise ValueError(
            "Reference input destination already exists; verify it or select a new snapshot id"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    archive = RemoteArchive()
    decompressed = {}
    with tempfile.TemporaryDirectory(prefix=".reference-inputs-", dir=output.parent) as work:
        staging = Path(work) / "inputs"
        staging.mkdir()
        for name in sorted(selected):
            raw = read_packed_file(archive, name, cache=decompressed)
            target = contained_path(staging, name, reject_symlinks=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        if digest(staging / "dataset.json") != expected_dataset_sha256:
            raise ValueError("Restored packed dataset differs from its pinned hash")
        receipt = {
            "schema_version": 1,
            "kind": "packed_reference_input_import",
            "id": identifier,
            "manifest_url": manifest_url,
            "manifest_sha256": expected_manifest_sha256,
            "dataset_sha256": expected_dataset_sha256,
            "input_file_count": len(selected),
            "downloaded_packs": sorted(
                name for name in archive.downloaded if name != "manifest.json"
            ),
        }
        (staging / "import-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(output)
    return output


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--packed-manifest-url")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--expected-dataset-sha256")
    parser.add_argument("--snapshot-id")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--source", type=Path, help="Approved local source copy; no network download"
    )
    action.add_argument(
        "--check",
        action="store_true",
        help="Verify every installed input against the manifest",
    )
    args = parser.parse_args(argv)
    if args.packed_manifest_url:
        if args.manifest or args.check or args.source:
            parser.error("--packed-manifest-url cannot be combined with local manifest actions")
        required = {
            "--expected-manifest-sha256": args.expected_manifest_sha256,
            "--expected-dataset-sha256": args.expected_dataset_sha256,
            "--snapshot-id": args.snapshot_id,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            parser.error("packed snapshot import requires " + ", ".join(missing))
        output = import_packed_snapshot(
            args.packed_manifest_url,
            args.expected_manifest_sha256,
            args.expected_dataset_sha256,
            args.snapshot_id,
        )
        print(output)
        return
    if args.manifest is None:
        parser.error("--manifest is required for local source and check actions")
    if not args.check and args.source is None:
        parser.error("select --source or --check")
    if args.check:
        manifest = load_manifest(args.manifest)
        root = input_root(manifest)
        if manifest["schema_version"] == 2:
            read_dataset_bytes(args.manifest.parent / manifest["dataset"])
        for relative, expected in manifest.get("files", {}).items():
            if digest(contained_path(root, relative, reject_symlinks=True)) != expected:
                raise ValueError("Reference input changed: " + relative)
        for tree, files in manifest["trees"].items():
            verify_tree(root / tree, files)
    else:
        import_inputs(args.manifest, args.source)
    print("Reference input inventory and hashes verified")


if __name__ == "__main__":
    main()
