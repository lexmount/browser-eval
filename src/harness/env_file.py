"""Parse dotenv files as data; each caller decides how to apply the values."""

import io
import re
from pathlib import Path

from dotenv import dotenv_values

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Return assignments without evaluating shell code or interpolating values.

    python-dotenv defines the syntax: an optional ``export`` prefix, matched
    quotes with escapes, and `` #`` comments after an unquoted value. The last
    assignment of a name wins. Mismatched quotes, names that are not shell
    identifiers and lines without ``=`` yield no value, so the caller reports
    the name as missing. A missing file raises ``FileNotFoundError``.
    """
    text = Path(path).expanduser().read_text(encoding="utf-8")
    parsed = dotenv_values(stream=io.StringIO(text), interpolate=False)
    return {name: value for name, value in parsed.items()
            if value is not None and _NAME.fullmatch(name)}
