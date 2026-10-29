"""Read report messages from language catalogs."""
import json
from functools import cache
from pathlib import Path


@cache
def _catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def translate(path: Path, key: str, language: str, **values):
    """Select one declared language; preserve values supplied by the report model."""
    try:
        message = _catalog(path)[language][key]
    except KeyError as exc:
        raise ValueError(f"Missing report translation: {language}/{key}") from exc
    if isinstance(message, list):
        return [item.format_map(values) for item in message]
    return message.format_map(values)
