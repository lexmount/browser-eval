"""Runtime configuration helpers for public WebArena Trajectory Replay."""

from __future__ import annotations

import html
import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

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


def site_password_spellings(passwords: Iterable[str]) -> list[str]:
    """Return each set password as written and under up to two text escapes, longest first."""
    escapes: tuple[Callable[[str], str], ...] = (
        html.escape,
        lambda text: html.escape(text, quote=False),
        lambda text: json.dumps(text)[1:-1],
        lambda text: json.dumps(text, ensure_ascii=False)[1:-1],
        lambda text: repr(text)[1:-1],
        # repr picks its quote from the whole message, so force the form that escapes apostrophes.
        lambda text: repr("\"'" + text)[4:-1],
    )
    spellings = {password for password in passwords if password.strip()}
    for _ in range(2):
        spellings |= {escape(spelling) for spelling in spellings for escape in escapes}
    return sorted(spellings, key=len, reverse=True)


def redact_site_passwords(text: str, passwords: Iterable[str]) -> str:
    """Replace every spelling of each set password, longest first so none leaves a remainder."""
    for spelling in site_password_spellings(passwords):
        text = text.replace(spelling, "[REDACTED]")
    return text


def map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    """Apply ``transform`` to every string value in nested JSON-like data, keeping its keys."""
    if isinstance(value, dict):
        return {key: map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [map_strings(item, transform) for item in value]
    if isinstance(value, tuple):
        return tuple(map_strings(item, transform) for item in value)
    if isinstance(value, str):
        return transform(value)
    return value


def configure_browser_docker_host(value: str | None) -> None:
    """Bind browser containers to an explicit absolute Unix Docker socket."""
    if value is None:
        return
    prefix = "unix://"
    if not value.startswith(prefix) or not Path(value[len(prefix) :]).is_absolute():
        raise ValueError("browser Docker host must be an absolute unix:// socket")
    os.environ["DOCKER_HOST"] = value
