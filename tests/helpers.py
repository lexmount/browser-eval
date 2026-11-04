"""Small helpers shared by test modules."""

import json
from pathlib import Path


def write_json(path: Path, value: object) -> Path:
    """Write UTF-8 JSON, creating the parent directory, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
