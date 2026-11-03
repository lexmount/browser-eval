"""English is the Search report source language; `messages.json` holds its Chinese translation."""
import json
from functools import cache
from pathlib import Path


@cache
def _catalog():
    return json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))


def _fill(text, values):
    return text.format(**values) if values else text


def zh_text(source, /, *, context=None, **values):
    """Translate English source text; `context` separates one English label with several Chinese meanings."""
    return _fill(_catalog()["zh"][f"{context}|{source}" if context else source], values)


def report_text(source, /, zh, *, context=None, **values):
    """Render English source text in the requested edition; `values` fill its named placeholders."""
    return zh_text(source, context=context, **values) if zh else _fill(source, values)
