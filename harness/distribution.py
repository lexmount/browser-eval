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
