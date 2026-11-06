"""The outer flow report release checks repeat; each track keeps its own rules."""

import inspect
import json
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
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


def _implementation():
    source = Path(__file__).parents[2]
    paths = [p for name in ('harness', 'lexbench_webfetch', 'lexbench_websearch')
             for p in (source / name).rglob('*')
             if p.suffix in {'.py', '.json', '.js', '.mjs'} and p.is_file()]
    paths.extend(p for p in (source.parent / 'uv.lock', source.parent / 'pyproject.toml')
                 if p.is_file())
    return canonical_json_sha256({str(p): file_sha256(p) for p in sorted(paths)})


@contextmanager
def full_validation():
    """Release boundaries always execute checks and never read or write receipts."""
    previous = _ACTIVE_CHECK.get()
    token = _ACTIVE_CHECK.set(False)
    try:
        if previous is not False:
            print('Report validation: full; 0 unchanged units reused (release/full audit).', file=sys.stderr)
        yield
    finally:
        _ACTIVE_CHECK.reset(token)


class _CheckSession:
    def __init__(self, root, track):
        self.root = root
        self.before = _inventory(root)
        self.identity = {'schema': 2, 'runtime': [sys.version, sys.flags.optimize],
                         'implementation': _implementation()}
        cache = data_home() / 'report-checks'
        self.disabled = cache.resolve().is_relative_to(root.resolve())
        self.path = cache / (canonical_json_sha256([track, str(root.parent.resolve())], allow_nan=False) + '.json')
        self.entries, self.used = set(), set()
        self.executed = self.reused = 0
        self.loaded = False
        try:
            if self.path.is_symlink():
                raise ValueError('Linked report check receipt')
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if (not self.disabled and saved['identity'] == self.identity
                    and isinstance(saved['passed'], list)
                    and all(isinstance(k, str) for k in saved['passed'])
                    and saved['sha256'] == canonical_json_sha256(saved['passed'])):
                self.entries = set(saved['passed'])
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
            if self.disabled or not self.used:
                raise OSError('No reusable routine checks to retain')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.parent.is_symlink():
                raise ValueError('Linked report check cache')
            passed = sorted(self.used)
            atomic_write(self.path, json.dumps({'identity': self.identity, 'passed': passed,
                                                'sha256': canonical_json_sha256(passed)}).encode('utf-8'))
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
    """Reuse routine validations; explicit release and complete-cohort checks stay fresh."""
    def decorate(check):
        signature = inspect.signature(check)
        @wraps(check)
        def run(root, *args, **kwargs):
            root = Path(root)
            if _ACTIVE_CHECK.get() is False:
                return check(root, *args, **kwargs)
            bound = signature.bind(root, *args, **kwargs)
            bound.apply_defaults()
            if (bound.arguments.get('release') or bound.arguments.get('require_complete')
                    or os.environ.get('BROWSER_EVAL_FULL_REPORT_CHECK') == '1'):
                with full_validation():
                    return check(root, *args, **kwargs)
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


def validation_step(check):
    """Reuse only successful, I/O-free, void checks with automatically bound arguments."""
    signature = inspect.signature(check)
    name = check.__module__ + '.' + check.__qualname__

    @wraps(check)
    def run(*args, **kwargs):
        session = _ACTIVE_CHECK.get()
        if not session or check.__closure__:
            return check(*args, **kwargs)
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        try:
            if not _json_value(bound.arguments):
                raise TypeError('Undeclared input type')
            key = canonical_json_sha256([name, bound.arguments], allow_nan=False, sort_keys=False)
        except (ValueError, TypeError):
            return check(*args, **kwargs)
        if key in session.entries:
            session.reused += 1
            session.used.add(key)
            return None
        session.executed += 1
        result = check(*args, **kwargs)
        if result is None:
            session.used.add(key)
            session.entries.add(key)
        return result
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
    validate_presentation(text)
    return BeautifulSoup(text, "html.parser")


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
