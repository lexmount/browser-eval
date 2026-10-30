"""Locate immutable files declared by the installed distribution."""

import importlib.metadata
from pathlib import Path


def installed_data_path(relative: str) -> Path:
    """Locate declared distribution data even when an installer uses an overlay."""
    requested = Path(relative)
    if requested.is_absolute() or '..' in requested.parts or not requested.parts:
        raise ValueError('Installed data requires a relative distribution path')
    distribution = importlib.metadata.distribution('browser-harness-eval')
    matches = [entry for entry in distribution.files or ()
               if entry.parts[-len(requested.parts):] == requested.parts]
    if len(matches) != 1:
        raise ValueError('Distribution must declare one data file: ' + relative)
    path = Path(distribution.locate_file(matches[0]))
    if path.is_symlink() or not path.is_file():
        raise ValueError('Installed distribution data is missing or symlinked: ' + relative)
    return path


def repository_root(package: Path | None = None) -> Path:
    """Locate checkout assets, or the installed package parent outside a checkout."""
    parent = (Path(__file__).resolve().parent if package is None else package).parent
    return parent.parent if parent.name == "src" and (parent.parent / "pyproject.toml").is_file() else parent


def source_path(relative: str | Path, *, root: Path | None = None) -> Path:
    """Resolve a logical source binding without changing its recorded identity."""
    root = repository_root() if root is None else Path(root)
    relative = Path(relative)
    if relative.parts and relative.parts[0] in {"harness", "lexbench_webfetch", "lexbench_websearch"}:
        if (root / "src" / "harness").is_dir():
            return root / "src" / relative
    candidate = root / relative
    if relative.as_posix() in {"pyproject.toml", "uv.lock"} and not candidate.is_file():
        return installed_data_path("share/browser-harness-eval/provenance/" + relative.name)
    return candidate
