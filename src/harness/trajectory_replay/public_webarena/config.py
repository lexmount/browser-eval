"""Runtime configuration helpers for browser-use evaluation."""

from __future__ import annotations

import os
import re
from pathlib import Path

from harness.distribution import repository_root

ROOT = repository_root()


def load_runtime_env(path: str | Path | None = None) -> None:
    """Load the repository-local dotenv file without evaluating shell code."""
    selected = Path(path) if path is not None else (ROOT / ".env")
    if path is None and not selected.exists():
        return
    if not selected.is_file():
        raise ValueError(f"browser-use environment file is missing: {selected}")
    for raw_line in selected.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def configure_browser_docker_host(value: str | None) -> None:
    """Bind browser containers to an explicit absolute Unix Docker socket."""
    if value is None:
        return
    prefix = "unix://"
    if not value.startswith(prefix) or not Path(value[len(prefix) :]).is_absolute():
        raise ValueError("browser Docker host must be an absolute unix:// socket")
    os.environ["DOCKER_HOST"] = value
