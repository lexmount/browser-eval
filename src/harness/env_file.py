"""Parse dotenv files as data; each caller decides how to apply the values."""

import io
import logging
import re
from pathlib import Path

from dotenv.main import with_warn_for_invalid_lines
from dotenv.parser import Binding, parse_stream

_LOGGER = logging.getLogger(__name__)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _read_bindings(path: str | Path) -> dict[str, Binding]:
    text = Path(path).expanduser().read_text(encoding="utf-8")
    bindings: dict[str, Binding] = {}
    for binding in with_warn_for_invalid_lines(parse_stream(io.StringIO(text))):
        if binding.key is None:
            continue
        if "\n" in binding.original.string.strip():
            # An unterminated quote closed by a later line's quote lands here too,
            # and it cannot be told apart from an intended multi-line value.
            _LOGGER.warning("Ignoring the multi-line value starting at line %s", binding.original.line)
            continue
        bindings[binding.key] = binding
    return {name: binding for name, binding in bindings.items()
            if binding.value is not None and _NAME.fullmatch(name)}


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Return assignments without evaluating shell code or interpolating values.

    python-dotenv defines the syntax: an optional ``export`` prefix, matched
    quotes with escapes, and `` #`` comments after an unquoted value. The last
    assignment of a name wins. Mismatched quotes, values spanning several lines
    (write ``\\n`` in double quotes instead), names that are not shell identifiers
    and lines without ``=`` yield no value, so the caller reports the name as
    missing. A missing file raises ``FileNotFoundError``.
    """
    return {name: binding.value for name, binding in _read_bindings(path).items()}
