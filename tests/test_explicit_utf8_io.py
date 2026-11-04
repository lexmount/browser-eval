"""Text files are UTF-8 on every host; the locale never selects their encoding."""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ('src', 'scripts', 'tests', 'examples', '.agents')
# Interpreter settings that keep the C locale's ASCII codec instead of upgrading to UTF-8.
ASCII_LOCALE = {'PYTHONUTF8': '0', 'PYTHONCOERCECLOCALE': '0', 'LC_ALL': 'C'}

SCORER = 'scoring_source_sha256 is frozen in released catalogs'
MIGRATED = 'after_sha256 in harness/benchmarks/code-migration.json'
SEARCH_SCORER = 'scoring_source_sha256 and collection_implementation of lexbench_websearch'
COLLECTION = 'collection_implementation must match to resume a Search cohort'
ORCHESTRATOR = 'orchestrator_sha256 is frozen in the resource protocol'
VISUAL = 'visual receipts bind checker_sources or producer_sources'
NATIVE_POLICY = 'answer_selection_policy source_sha256 is in the frozen native route baseline'
# Editing these moves a hash that frozen evidence compares with the current source.
# The count is the number of calls left implicit, so the exemption cannot grow unseen.
HASH_BOUND = {
    'src/harness/lexbench/resource_matrix.py': (5, ORCHESTRATOR),
    'src/harness/lexbench/resource_profiles.py': (7, ORCHESTRATOR),
    'src/harness/reporting/visual.py': (1, VISUAL),
    'src/harness/web_fetch/native_projection.py': (2, NATIVE_POLICY),
    'src/harness/trajectory_replay/webarena_report_visual.py': (1, VISUAL),
    'src/harness/web_search/runner.py': (7, COLLECTION),
    'src/harness/web_search/visual.py': (2, VISUAL),
    'src/lexbench_webfetch/cli.py': (3, MIGRATED),
    'src/lexbench_webfetch/scoring.py': (1, SCORER),
    'src/lexbench_webfetch/scoring_regressions.py': (3, MIGRATED),
    'src/lexbench_websearch/qualification.py': (1, SEARCH_SCORER),
    'src/lexbench_websearch/relevance.py': (1, SEARCH_SCORER),
    'src/lexbench_websearch/rubric.py': (2, SEARCH_SCORER),
}
LEXBENCH_BUILD = '''
import os, sys
from pathlib import Path
import pytest
from harness.lexbench import report
from tests.lexbench_report_fixture import build_report_inputs, pin_vendor_assets
root = Path(sys.argv[1])
os.chdir(root)
pin_vendor_assets(pytest.MonkeyPatch())
report.main(build_report_inputs(root, sys.argv[2]))
'''
TEMPORARY_FILES = {'NamedTemporaryFile', 'TemporaryFile', 'SpooledTemporaryFile'}
# These are called as `open(path, mode)`, so the mode is the second argument.
TEXT_BY_DEFAULT_MODULES = {None, 'io', 'builtins'}
BINARY_BY_DEFAULT_MODULES = {'gzip', 'bz2', 'lzma'}
# `.open` on these names is not a file-object constructor with an encoding parameter.
OTHER_OPENERS = {'os', 'tarfile', 'Image', 'zipfile', 'webbrowser', 'opener'}


def _mode(call, position):
    for keyword in call.keywords:
        if keyword.arg == 'mode':
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _may_match(mode, matches):
    """A mode that is unknown until run time counts as a match, so the guard fails closed."""
    if isinstance(mode, ast.IfExp):
        return _may_match(mode.body, matches) or _may_match(mode.orelse, matches)
    return not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)) or matches(mode.value)


def _may_be_text(mode):
    return _may_match(mode, lambda value: 'b' not in value)


def _may_be_compressed_text(mode):
    """Compressed openers treat a bare "r" as binary and need an explicit "t" for text."""
    return _may_match(mode, lambda value: 't' in value)


def _needs_encoding(call):
    if any(keyword.arg in ('encoding', None) for keyword in call.keywords):
        return False
    function = call.func
    if isinstance(function, ast.Name):
        name, receiver = function.id, None
    elif isinstance(function, ast.Attribute):
        name, receiver = function.attr, ast.unparse(function.value)
    else:
        return False
    if name == 'read_text':
        return not call.args
    if name == 'write_text':
        return len(call.args) < 2
    if name == 'TextIOWrapper':
        return len(call.args) < 2
    if name in TEMPORARY_FILES:
        mode = _mode(call, 0)
        return mode is not None and _may_be_text(mode)
    if name == 'fdopen' or (name == 'open' and receiver in TEXT_BY_DEFAULT_MODULES):
        mode = _mode(call, 1)
        return mode is None or _may_be_text(mode)
    if name != 'open':
        return False
    if receiver in BINARY_BY_DEFAULT_MODULES:
        mode = _mode(call, 1)
        return mode is not None and _may_be_compressed_text(mode)
    if receiver.split('.')[-1] not in OTHER_OPENERS and 'build_opener' not in receiver:
        mode = _mode(call, 0)
        return mode is None or _may_be_text(mode)
    return False


def implicit_encoding_lines(source):
    return sorted(node.lineno for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Call) and _needs_encoding(node))


def _python_files():
    for directory in SCANNED:
        for path in sorted((ROOT / directory).rglob('*.py')):
            if '__pycache__' not in path.parts:
                yield path


def test_every_text_read_and_write_names_its_encoding():
    found = {}
    for path in _python_files():
        lines = implicit_encoding_lines(path.read_text(encoding='utf-8'))
        if lines:
            found[path.relative_to(ROOT).as_posix()] = lines
    assert {name: len(lines) for name, lines in found.items()} == {
        name: count for name, (count, _) in HASH_BOUND.items()}, found


@pytest.mark.parametrize('source', [
    'path.read_text()\n',
    'path.write_text(value)\n',
    'open(path)\n',
    'open(path, "a")\n',
    'path.open()\n',
    'path.open("w")\n',
    'path.open(mode="r+")\n',
    'os.fdopen(descriptor, "w")\n',
    'tempfile.NamedTemporaryFile("w")\n',
    'tempfile.NamedTemporaryFile(mode="w+")\n',
    'tempfile.NamedTemporaryFile(mode)\n',
    'open(path, mode)\n',
    'open(path, mode=mode)\n',
    'path.open(mode)\n',
    'path.open(newline="")\n',
    'path.open("r+" if existing else "wb")\n',
    'io.open(path)\n',
    'io.open(path, "w")\n',
    'io.open(path, mode)\n',
    'builtins.open(path)\n',
    'builtins.open(path, "a")\n',
    'gzip.open(path, "rt")\n',
    'gzip.open(path, mode="wt")\n',
    'gzip.open(path, mode)\n',
    'bz2.open(path, "rt")\n',
    'lzma.open(path, "rt")\n',
    'io.TextIOWrapper(stream)\n',
    'TextIOWrapper(stream, newline="")\n',
])
def test_guard_rejects_locale_dependent_text_io(source):
    assert implicit_encoding_lines(source) == [1]


@pytest.mark.parametrize('source', [
    'path.read_text(encoding="utf-8")\n',
    'path.read_text("utf-8")\n',
    'path.write_text(value, encoding="utf-8")\n',
    'path.write_text(value, "utf-8")\n',
    'path.read_text(**options)\n',
    'open(path, "rb")\n',
    'open(path, encoding="utf-8")\n',
    'path.open("xb")\n',
    'path.open("r+b" if existing else "wb")\n',
    'os.fdopen(descriptor, "wb")\n',
    'os.open(path, os.O_RDONLY)\n',
    'tempfile.NamedTemporaryFile(dir=parent)\n',
    'tempfile.TemporaryFile()\n',
    'tarfile.open(fileobj=stream, mode="r:gz")\n',
    'io.open(path, "rb")\n',
    'builtins.open(path, "wb")\n',
    'gzip.open(path)\n',
    'gzip.open(path, "r")\n',
    'gzip.open(path, "wb")\n',
    'gzip.open(path, "rt", encoding="utf-8")\n',
    'bz2.open(path, "rb")\n',
    'lzma.open(path, "rb")\n',
    'lzma.open(path, "wt", encoding="utf-8")\n',
    'io.TextIOWrapper(stream, encoding="utf-8")\n',
    'io.TextIOWrapper(stream, "utf-8")\n',
    'Image.open(path)\n',
    'opener.open(request, timeout=5)\n',
    # importlib.metadata.Distribution.read_text takes a file name and has no encoding.
    'distribution.read_text("direct_url.json")\n',
])
def test_guard_keeps_binary_and_explicit_io(source):
    assert implicit_encoding_lines(source) == []


def ascii_locale_environment():
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith('PYTEST_')}
    return {**environment, **ASCII_LOCALE}


def test_ascii_locale_environment_really_selects_ascii():
    probe = 'import locale, sys; print(locale.getencoding(), sys.flags.utf8_mode)'
    result = subprocess.run([sys.executable, '-c', probe], env=ascii_locale_environment(),
                            capture_output=True, encoding='utf-8', check=True)
    name, utf8_mode = result.stdout.split()
    assert utf8_mode == '0'
    assert 'utf' not in name.lower()


def test_report_builds_are_byte_identical_under_an_ascii_locale(tmp_path):
    """Every track's multilingual build must match the snapshot taken under UTF-8."""
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', 'tests/test_golden_reports.py',
         '-q', '-p', 'no:cacheprovider', '--basetemp', str(tmp_path / 'build')],
        cwd=ROOT, env=ascii_locale_environment(),
        capture_output=True, encoding='utf-8', errors='replace', check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('scenario', ['minimal', 'macos', 'linux'])
def test_lexbench_report_builds_under_an_ascii_locale(tmp_path, scenario):
    """The whole command succeeds with an ASCII stdout and writes non-ASCII chart text as UTF-8."""
    result = subprocess.run(
        [sys.executable, '-c', LEXBENCH_BUILD, str(tmp_path), scenario],
        cwd=ROOT, env=ascii_locale_environment(),
        capture_output=True, encoding='utf-8', errors='replace', check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    charts = sorted((tmp_path / 'report/assets').glob('*-zh.svg'))
    assert charts
    for chart in charts:
        assert not chart.read_text(encoding='utf-8').isascii()
