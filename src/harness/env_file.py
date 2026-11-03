"""Parse dotenv files as data; each caller decides how to apply the values."""

import functools
import io
import logging
import os
import re
import stat
from pathlib import Path

from dotenv.main import with_warn_for_invalid_lines
from dotenv.parser import Binding, parse_stream

from harness.distribution import repository_root

_LOGGER = logging.getLogger(__name__)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# python-dotenv reports no key for a statement it rejects, so read the name it assigns.
_REJECTED_NAME = re.compile(r"""\s*(?:export[^\S\r\n]+)?(?:'([^']+)'|([^=#\s]+))[^\S\r\n]*=""")


def _read_bindings(path: str | Path) -> dict[str, Binding]:
    selected = Path(path).expanduser()
    # Reading a FIFO would block forever; stat also keeps the missing-file error.
    if not stat.S_ISREG(selected.stat().st_mode):
        raise ValueError(f"env file must be a regular file: {selected.name}")
    text = selected.read_text(encoding="utf-8")
    bindings: dict[str, Binding] = {}
    for binding in with_warn_for_invalid_lines(parse_stream(io.StringIO(text))):
        if binding.key is None:
            if binding.error and (name := _REJECTED_NAME.match(binding.original.string)):
                bindings.pop(name[1] or name[2], None)
            continue
        if "\n" in binding.original.string.strip():
            # An unterminated quote closed by a later line's quote lands here too,
            # and it cannot be told apart from an intended multi-line value.
            _LOGGER.warning("Ignoring the multi-line value starting at line %s", binding.original.line)
            bindings.pop(binding.key, None)
            continue
        bindings[binding.key] = binding
    return {name: binding for name, binding in bindings.items()
            if binding.value is not None and _NAME.fullmatch(name)}


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Return assignments without evaluating shell code or interpolating values.

    python-dotenv defines the syntax: an optional ``export`` prefix, matched
    quotes with escapes, and `` #`` comments after an unquoted value. The last
    statement of a name decides its value. Mismatched quotes, values spanning
    several lines (write ``\\n`` in double quotes instead) and a bare ``NAME`` line
    yield no value and remove any earlier value of that name, so the caller reports
    the name as missing instead of running on a stale value. Only the name that
    opens a multi-line value is affected; a name on a line it absorbed keeps its
    earlier value. Names that are not shell identifiers and lines that assign
    nothing are ignored. A missing file raises ``FileNotFoundError``, and a FIFO, directory
    or other non-regular file raises ``ValueError`` without being read.
    """
    return {name: binding.value for name, binding in _read_bindings(path).items()}


def read_env_statements(path: str | Path) -> dict[str, str]:
    """Return the source text of each assignment ``parse_env_file`` would read, for copying."""
    return {name: binding.original.string.strip() for name, binding in _read_bindings(path).items()}


_selected_env_file: Path | None = None


def select_env_file(path: str | Path | None) -> None:
    """Choose the file ``local_setting`` reads for this process; ``None`` means the default."""
    global _selected_env_file
    _selected_env_file = None if path is None else Path(path).expanduser()


@functools.lru_cache(maxsize=8)
def _parsed_settings(path: Path, modified_ns: int, size: int) -> dict[str, str]:
    return parse_env_file(path)


def local_setting(
    name: str, env_file: str | Path | None = None, *, follow_links: bool = False
) -> str:
    """Return one setting unchanged, or an empty string when it is unset.

    An exported variable wins, including an empty one. Otherwise the value comes from
    ``env_file``, the file chosen with ``select_env_file``, or the repository's ``.env``.
    An absent file means the setting is unset. A link passed or selected explicitly, a
    non-regular file and an unreadable file stop with an error when a setting is first needed.
    """
    exported = os.environ.get(name)
    if exported is not None:
        return exported
    chosen = env_file if env_file is not None else _selected_env_file
    path = Path(chosen).expanduser() if chosen is not None else repository_root() / ".env"
    if chosen is not None and path.is_symlink() and not follow_links:
        raise ValueError("local settings file must be a regular file")
    return _file_setting(path, name)


def _file_setting(path: Path, name: str) -> str:
    try:
        state = path.stat()
        return _parsed_settings(path, state.st_mtime_ns, state.st_size).get(name, "") or ""
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot read local settings file {path.name}") from error


# Tests set this to a synthetic file; an environment variable would reopen the shell route.
_repository_credentials_file: Path | None = None


def repository_credential(name: str) -> str:
    """Return one evaluation credential unchanged from the repository ``.env`` only.

    Neither the process environment nor ``select_env_file`` can supply it, so a run and
    the redaction of its public output always use the same value.
    """
    return _file_setting(_repository_credentials_file or repository_root() / ".env", name)

