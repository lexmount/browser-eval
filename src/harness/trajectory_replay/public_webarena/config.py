"""Runtime configuration helpers for public WebArena Trajectory Replay."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from harness.distribution import repository_root
from harness.env_file import parse_env_file

ROOT = repository_root()


def load_runtime_env(path: str | Path | None = None) -> None:
    """Fill unset process settings from a dotenv file without evaluating shell code.

    ``path`` selects the file and defaults to the repository ``.env``. An exported variable
    wins over either file. This covers ordinary settings only: site logins, the Rainbond API
    token and the LLM diagnostics key come from the repository ``.env`` alone, so neither an
    exported value nor a selected file can change them.
    """
    selected = Path(path) if path is not None else (ROOT / ".env")
    if path is None and not selected.exists():
        return
    if not selected.is_file():
        raise ValueError(f"Trajectory Replay environment file is missing: {selected}")
    for key, value in parse_env_file(selected).items():
        os.environ.setdefault(key, value)


def redact_site_passwords(text: str, passwords: Iterable[str]) -> str:
    """Replace each set password in ``text``, longest first so none leaves a remainder."""
    for password in sorted({item for item in passwords if item.strip()}, key=len, reverse=True):
        text = text.replace(password, "[REDACTED]")
    return text


def configure_browser_docker_host(value: str | None) -> None:
    """Bind browser containers to an explicit absolute Unix Docker socket."""
    if value is None:
        return
    prefix = "unix://"
    if not value.startswith(prefix) or not Path(value[len(prefix) :]).is_absolute():
        raise ValueError("browser Docker host must be an absolute unix:// socket")
    os.environ["DOCKER_HOST"] = value
