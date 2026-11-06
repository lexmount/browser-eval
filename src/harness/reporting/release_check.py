"""The outer flow report release checks repeat; each track keeps its own rules."""

import hashlib
import inspect
import json
import marshal
import os
import sys
from contextvars import ContextVar
from functools import lru_cache, wraps
from pathlib import Path

from bs4 import BeautifulSoup

from harness.data_home import data_home
from harness.record_io import canonical_json_sha256, file_sha256
from harness.report_languages import generated_languages
from harness.reporting.presentation import check_presentation
from harness.reporting.templates import check_template_identity
from harness.reporting.versions import check_build_version
from harness.web_fetch.safety import reject_evidence_symlinks

_ACTIVE_CHECK = ContextVar('report_validation', default=None)


def _json_value(value):
    if value is None or type(value) in (str, bool, int, float):
        return True
    if type(value) is list:
        return all(_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_value(item) for key, item in value.items())
    return False


def _inventory(root):
    reject_evidence_symlinks(root)
    return {p.relative_to(root).as_posix(): file_sha256(p)
            for p in sorted(root.rglob('*')) if p.is_file()}


@lru_cache(maxsize=4)
def _implementation_bytes(signatures):
    return canonical_json_sha256({name: file_sha256(Path(name)) for name, _ in signatures}, allow_nan=False)


def _implementation():
    source = Path(__file__).parents[2]
    paths = [p for name in ('harness', 'lexbench_webfetch', 'lexbench_websearch')
             for p in (source / name).rglob('*')
             if p.is_file() and p.suffix in {'.py', '.json', '.js', '.mjs'}]
    paths.extend(p for p in (source.parent / 'uv.lock', source.parent / 'pyproject.toml')
                 if p.is_file())
    signatures = []
    for path in sorted(paths):
        info = path.stat()
        signatures.append((str(path), (info.st_dev, info.st_ino, info.st_size,
                                       info.st_mtime_ns, info.st_ctime_ns)))
    if os.name == 'nt':
        return canonical_json_sha256({name: file_sha256(Path(name)) for name, _ in signatures}, allow_nan=False)
    return _implementation_bytes(tuple(signatures))


class _CheckSession:
    def __init__(self, root, track):
        self.root = root
        self.before = _inventory(root)
        self.identity = {'schema': 1, 'runtime': sys.version, 'implementation': _implementation()}
        cache = Path(os.environ.get('BROWSER_EVAL_REPORT_CHECK_CACHE', data_home() / 'report-checks'))
        self.disabled = cache.resolve().is_relative_to(root.resolve())
        self.path = cache / (canonical_json_sha256([track, str(root.parent.resolve())], allow_nan=False) + '.json')
        self.entries, self.used = {}, {}
        self.executed = self.reused = 0
        self.loaded = False
        self.forced = os.environ.get('BROWSER_EVAL_FULL_REPORT_CHECK') == '1'
        try:
            if self.path.is_symlink():
                raise ValueError('Linked report check receipt')
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if (not self.forced and not self.disabled and saved['identity'] == self.identity
                    and isinstance(saved['entries'], dict)
                    and all(isinstance(k, str) and isinstance(v, str)
                            for k, v in saved['entries'].items())
                    and saved['sha256'] == canonical_json_sha256(saved['entries'], allow_nan=False)):
                self.entries = saved['entries']
                self.loaded = True
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def finish(self):
        if _inventory(self.root) != self.before:
            raise ValueError('Report changed during validation')
        if _implementation() != self.identity['implementation']:
            raise ValueError('Report checker changed during validation')
        from harness.reporting.publication import atomic_write
        try:
            if self.disabled:
                raise OSError('Report check receipts must stay outside the report')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            reject_evidence_symlinks(self.path.parent)
            atomic_write(self.path, json.dumps({'identity': self.identity, 'entries': self.used,
                                                'sha256': canonical_json_sha256(self.used, allow_nan=False)}).encode('utf-8'))
            self.path.chmod(0o600)
        except (OSError, ValueError):
            # A read-only machine runs the same checks without persisting reuse.
            pass
        mode = 'incremental' if self.loaded else 'full'
        print(f'Report validation: {mode}; {self.executed} units checked, '
              f'{self.reused} unchanged units reused. Integrity, privacy, bindings and links '
              'retain their owning checks. BROWSER_EVAL_FULL_REPORT_CHECK=1 forces a full audit.',
              file=sys.stderr)


def scoped_check(track):
    """Share successful pure validation units across normal check/build/release calls."""
    def decorate(check):
        @wraps(check)
        def run(root, *args, **kwargs):
            root = Path(root)
            if _ACTIVE_CHECK.get() is not None:
                return check(root, *args, **kwargs)
            try:
                session = _CheckSession(root, track)
            except (OSError, ValueError, TypeError):
                # Unknown/unfingerprintable inputs keep the original complete check.
                return check(root, *args, **kwargs)
            token = _ACTIVE_CHECK.set(session)
            try:
                result = check(root, *args, **kwargs)
                session.finish()
                return result
            finally:
                _ACTIVE_CHECK.reset(token)
        return run
    return decorate


def validated(name, inputs, validate):
    """Reuse a pure check only for identical explicit inputs and implementation.

    Validators that read files, launch browsers or inspect mutable global state
    remain outside this boundary. Their bindings/privacy/link checks always run.
    Unsupported input or return types conservatively execute without caching.
    """
    session = _ACTIVE_CHECK.get()
    if session is None:
        return validate()
    try:
        if not _json_value(inputs) or not hasattr(validate, '__code__'):
            raise TypeError('Undeclared input or validator type')
        code = hashlib.sha256(marshal.dumps(validate.__code__)).hexdigest()
        key = canonical_json_sha256([name, code, inputs], allow_nan=False)
    except (ValueError, TypeError):
        session.executed += 1
        return validate()
    if not session.forced and key in session.entries:
        try:
            result = json.loads(session.entries[key])
        except (ValueError, TypeError):
            pass
        else:
            session.reused += 1
            session.used[key] = session.entries[key]
            return result
    session.executed += 1
    result = validate()
    try:
        encoded = json.dumps(result, allow_nan=False)
        if not _json_value(result):
            return result
        session.used[key] = encoded
        if not session.forced:
            session.entries[key] = encoded
    except (ValueError, TypeError):
        pass
    return result


def validation_step(check):
    """Declare an I/O-free validator whose complete inputs are its arguments."""
    signature = inspect.signature(check)
    implementation = hashlib.sha256(marshal.dumps(check.__code__)).hexdigest()

    @wraps(check)
    def run(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return validated(check.__module__ + '.' + check.__qualname__ + ':' + implementation, bound.arguments,
                         lambda: check(*args, **kwargs))
    return run


@validation_step
def validate_presentation(text):
    """Validate markup without returning a mutable DOM or caching a browser run."""
    check_presentation(text)


def open_report(root) -> Path:
    """Refuse a linked report tree before any rule reads from it."""
    root = Path(root)
    reject_evidence_symlinks(root)
    return root.resolve()


def read_page(path) -> BeautifulSoup:
    return BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")


def checked_page(path) -> BeautifulSoup:
    """Read one page through the shared comparison and evidence presentation rules."""
    text = Path(path).read_text(encoding="utf-8")
    parsed = []

    def validate():
        parsed.append(check_presentation(text))

    validated('presentation', [text], validate)
    return parsed[0] if parsed else BeautifulSoup(text, "html.parser")


def language_editions(root: Path) -> tuple[tuple[str, str], ...]:
    """Name each generated edition and its route prefix; Chinese stays at the root."""
    return tuple((language, "en/" if language == "en" else "")
                 for language in generated_languages(root))


def check_report_identity(root: Path, manifest: dict, track: str, profile_path: Path | None = None,
                          *, allow_unbound_profile: bool = False) -> None:
    """Bind the build, the maintained template and, when selected, the instance profile.

    ``allow_unbound_profile`` keeps reports from before profile binding readable.
    """
    check_build_version(root, manifest)
    check_template_identity(manifest, track)
    if profile_path is None:
        return
    bound = manifest.get("profile_sha256")
    if allow_unbound_profile and not bound:
        return
    if profile_path.is_symlink() or bound != file_sha256(profile_path):
        raise ValueError("report was built from a different instance profile")
