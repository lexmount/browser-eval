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


def catalog_text(path: Path, language: str):
    """Bind `t(id, english, **values)`: English is inline source text, other languages read the catalog."""
    def t(key: str, source: str, /, **values) -> str:
        if language == "en":
            return source.format_map(values)
        return translate(path, key, language, **values)
    return t
