"""Resolve the persistent home for measured data that must not live in Git."""

import os
from pathlib import Path


def configured_data_home() -> Path | None:
    """Return the explicitly configured home; an empty value counts as unset."""
    configured = os.environ.get("BROWSER_EVAL_DATA_HOME")
    return Path(configured).expanduser().resolve() if configured else None


def data_home() -> Path:
    """Read the environment at each call and return an absolute, resolved path."""
    return configured_data_home() or (Path.home() / ".browser-eval").resolve()
