"""Pure path, task identifier and input URL validation."""
import re
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

def safe_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError('Identifiers must be a single alphanumeric filename segment')
    return value


def contained_path(root: Path, relative: str | Path, *, reject_symlinks: bool = False) -> Path:
    value = str(relative)
    path = Path(value)
    if (not value or path == Path('.') or path.is_absolute() or '..' in path.parts
            or '\\' in value or PureWindowsPath(value).drive
            or any(ord(char) < 32 for char in value)):
        raise ValueError('Paths must be relative and stay inside the evidence directory')
    base = root.resolve()
    if reject_symlinks:
        if root.is_symlink():
            raise ValueError('Symlinked evidence is forbidden')
        lexical = base
        for part in path.parts:
            lexical = lexical / part
            if lexical.is_symlink():
                raise ValueError('Symlinked evidence is forbidden')
    target = (base / path).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ValueError('Path or symlink escapes its evidence directory')
    return target


def validate_http_url(url: str) -> str:
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url) or '\\' in url:
        raise ValueError('Expected an absolute HTTP(S) URL without control characters')
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Only absolute HTTP(S) URLs are allowed')
    return url

