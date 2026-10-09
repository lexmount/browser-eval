"""Validation at filesystem and URL boundaries shared by runners and reports."""
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit


def unlinked_path(path: Path) -> Path:
    """Normalize system aliases while rejecting user-created file and parent links."""
    absolute = Path(path).absolute()
    if sys.platform == 'darwin':
        for alias in (Path('/tmp'), Path('/var')):
            canonical = Path('/private') / alias.name
            if (absolute.is_relative_to(alias) and alias.is_symlink()
                    and alias.resolve() == canonical):
                absolute = canonical / absolute.relative_to(alias)
                break
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError('symlinked paths are forbidden')
    return Path(os.path.abspath(absolute))


def reject_evidence_symlinks(root: Path) -> None:
    """Never follow file or directory links while materializing evidence."""
    if root.is_symlink():
        raise ValueError('Evidence root must not be a symlink')
    if any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('Evidence tree contains a symlink')


def evidence_tree_identity(root: Path) -> dict[str, str]:
    """Bind every file of an evidence tree without following links."""
    reject_evidence_symlinks(root)
    if not root.is_dir():
        raise ValueError('Evidence root must be an existing directory')
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def stage_evidence_revision(source: Path, output: Path, operation):
    """Expose a new revision only after staging succeeds and its source remains unchanged."""
    if output.exists() or output.is_symlink():
        raise ValueError('Correction output already exists')
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError('Correction output must be outside its source evidence')
    before = evidence_tree_identity(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'.{output.name}-revision-', dir=output.parent) as work:
        staging = Path(work) / 'evidence'
        result = operation(staging)
        if evidence_tree_identity(source) != before:
            raise ValueError('Source evidence changed during revision')
        if output.exists() or output.is_symlink():
            raise ValueError('Correction output appeared during staging')
        staging.rename(output)
    return result


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


def commit_staged_evidence(source: Path, staged: Path, backup: Path) -> None:
    """Install a validated replacement, retaining the sole original on recovery failure."""
    source.rename(backup)
    try:
        staged.rename(source)
    except BaseException:
        try:
            backup.rename(source)
        except BaseException as recovery_error:
            raise RuntimeError(
                f"Rollback failed; original data retained at {backup}"
            ) from recovery_error
        raise
