from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .models import EvalCase


def load_env_file(path: Path) -> None:
    """Load simple dotenv assignments without evaluating shell code."""
    for raw_line in path.expanduser().read_text(encoding="utf-8").splitlines():
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


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}", value)
        if match:
            return os.getenv(match.group(1), match.group(2) or "")
        return value
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_dataset(path: Path) -> tuple[dict[str, Any], list[EvalCase]]:
    payload = load_json(path)
    if payload.get("tool") != "web_fetch":
        raise ValueError("dataset.tool must be 'web_fetch'")
    cases = [EvalCase(**item) for item in payload.get("cases", [])]
    if not cases:
        raise ValueError("dataset must contain at least one case")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("dataset case ids must be unique")
    return payload, cases


def load_provider_config(path: Path) -> dict[str, dict[str, Any]]:
    payload = expand_env(load_json(path))
    providers = payload.get("providers", {})
    if not providers:
        raise ValueError("provider config must define providers")
    return providers
