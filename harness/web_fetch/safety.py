"""Validation at filesystem and URL boundaries shared by runners and reports."""
import re
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit


def reject_evidence_symlinks(root: Path) -> None:
    """Never follow file or directory links while materializing evidence."""
    if root.is_symlink():
        raise ValueError('Evidence root must not be a symlink')
    if any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('Evidence tree contains a symlink')


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


def safe_href(url: str) -> str:
    """Allow HTTP(S) or local report links, never executable URI schemes."""
    if not isinstance(url, str) or not url or '\\' in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        return '#'
    try:
        parsed = urlsplit(url)
        if parsed.scheme:
            return validate_http_url(url)
        if parsed.netloc or url.lstrip().startswith('/'):
            return '#'
        return url
    except ValueError:
        return '#'


def validate_report_ids(dataset, records):
    for case in dataset['cases']:
        safe_identifier(case['id'])
    for record in records:
        for key in ['provider', 'case_id', 'attempt_id']:
            safe_identifier(record[key])
