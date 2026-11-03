"""Runtime configuration helpers for public WebArena Trajectory Replay."""

from __future__ import annotations

import os
from pathlib import Path

from harness.distribution import repository_root
from harness.env_file import parse_env_file

ROOT = repository_root()


def load_runtime_env(path: str | Path | None = None) -> None:
    """Load the repository-local dotenv file without evaluating shell code."""
    selected = Path(path) if path is not None else (ROOT / ".env")
    if path is None and not selected.exists():
        return
    if not selected.is_file():
        raise ValueError(f"Trajectory Replay environment file is missing: {selected}")
    for key, value in parse_env_file(selected).items():
        os.environ.setdefault(key, value)


def configure_browser_docker_host(value: str | None) -> None:
    """Bind browser containers to an explicit absolute Unix Docker socket."""
    if value is None:
        return
    prefix = "unix://"
    if not value.startswith(prefix) or not Path(value[len(prefix) :]).is_absolute():
        raise ValueError("browser Docker host must be an absolute unix:// socket")
    os.environ["DOCKER_HOST"] = value
