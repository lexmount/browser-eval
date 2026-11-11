"""Locate root-level definitions from a checkout or from the installed package."""

import importlib.resources
from pathlib import Path, PurePosixPath

_BUNDLED_DATA = "distribution_data"


def bundled_data_path(relative: str) -> Path:
    """Locate a file named by `tool.webstone.bundled-data` in pyproject.toml."""
    requested = PurePosixPath(relative)
    if requested.is_absolute() or ".." in requested.parts or not requested.parts:
        raise ValueError("Bundled data requires a relative repository path")
    root = repository_root()
    if (root / "src" / "harness").is_dir():
        return root / relative
    # `harness` is a namespace package; its traversable accepts one child at a time.
    resource = importlib.resources.files("harness") / _BUNDLED_DATA
    for part in requested.parts:
        resource = resource / part
    if not isinstance(resource, Path):
        raise ValueError("Bundled data requires an unpacked harness installation")
    if resource.is_symlink():
        raise ValueError("Bundled data must not be symlinked: " + relative)
    if not resource.is_file():
        raise FileNotFoundError("Installed package lacks bundled data: " + relative)
    return resource


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
        return bundled_data_path(relative.name)
    return candidate
