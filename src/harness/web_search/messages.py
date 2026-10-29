"""Read localized Search interface text without embedding it in renderer code."""
import json
from functools import cache
from pathlib import Path


@cache
def _catalog():
    return json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))

def report_text(key):
    return _catalog()["zh"][key]
