from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .models import EvalCase
from .retrieval_hints import parse_retrieval_hints, native_default_case

ROOT = Path(__file__).resolve().parents[2]


def _validate_retrieval_hints(item: dict[str, Any]) -> None:
    if native_default_case(item):
        return
    parse_retrieval_hints(
        str(item.get("track", "web")),
        item.get("retrieval_hints") if "retrieval_hints" in item else None,
        identifier=str(item.get("id", "<unknown>")),
    )


def _validate_ground_truth_evidence(item: dict[str, Any]) -> None:
    """Reject copied provenance links for references captured from the case URL."""
    case_host = (urlsplit(str(item.get("url", ""))).hostname or "").removeprefix("www.")
    for evidence in item.get("ground_truth_evidence", []):
        if evidence.get("supports") != "本版独立获取的完整参考正文":
            continue
        source_host = (urlsplit(str(evidence.get("source", ""))).hostname or "").removeprefix("www.")
        if not case_host or source_host != case_host:
            raise ValueError(
                f"{item.get('id', '<unknown>')} full-reference evidence must use the case URL host"
            )


def _evaluation_credential(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9_]+_(?:API_KEY|API_TOKEN|ACCOUNT_ID)", name))


def load_runtime_env(path: Path | None = None) -> None:
    """Use the repository .env by default; explicit missing files are errors."""
    selected = path if path is not None else ROOT / ".env"
    if path is not None and not selected.exists():
        raise FileNotFoundError(selected)
    # Credentials may never leak into a run from the parent shell.
    for key in list(os.environ):
        if _evaluation_credential(key):
            os.environ.pop(key)
    if path is not None or selected.exists():
        load_env_file(selected, override_credentials=True,
                      credentials_allowed=selected.resolve() == (ROOT / ".env").resolve())


def load_env_file(path: Path, *, override_credentials: bool = False, credentials_allowed: bool = True) -> None:
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
        if _evaluation_credential(key) and not credentials_allowed:
            raise ValueError("Evaluation credentials must come from the repository .env")
        if override_credentials and _evaluation_credential(key):
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}", value)
        if match:
            return os.getenv(match.group(1)) or match.group(2) or ""
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
    raw_cases = payload.get("cases", [])
    for item in raw_cases:
        _validate_retrieval_hints(item)
        _validate_ground_truth_evidence(item)
    cases = [EvalCase(**item) for item in raw_cases]
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
