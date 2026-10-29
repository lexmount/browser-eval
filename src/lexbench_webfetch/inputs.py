"""Pure frozen-input validation, independent of execution and report storage."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from .paths import contained_path, safe_identifier

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



def definition_path(relative: str) -> Path:
    """Resolve the sole installed owner of versioned benchmark definitions."""
    from harness.benchmarks.data import definition_path as resolve
    return resolve("webfetch", relative)



def data_home() -> Path:
    configured = os.environ.get('LEXBENCH_DATA_HOME') or os.environ.get('BROWSER_EVAL_DATA_HOME')
    return Path(configured).expanduser().resolve() if configured else Path.home()/'.lexbench-webfetch'


def require_mutable_inputs(dataset: Path, *, input_home: Path | None = None) -> None:
    path = dataset.parent / 'source-inputs.json'
    if path.exists() or path.is_symlink() or dataset.resolve().is_relative_to(
            input_home or data_home()/'reference-inputs'):
        raise ValueError('Pinned reference inputs are immutable; create a separate candidate dataset and sources')
