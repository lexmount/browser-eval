"""Read report messages from language catalogs."""
from functools import cache
from pathlib import Path

from harness.record_io import read_json


@cache
def _catalog(path: Path) -> dict:
    return read_json(path)


def translate(path: Path, key: str, language: str, **values):
    """Select one declared language; preserve values supplied by the report model."""
    try:
        message = _catalog(path)[language][key]
    except KeyError as exc:
        raise ValueError(f"Missing report translation: {language}/{key}") from exc
    if isinstance(message, list):
        return [item.format_map(values) for item in message]
    return message.format_map(values)
