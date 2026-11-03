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
PUBLISHED_COPY = 'copied byte for byte into the public reproduction directory'
VISUAL = 'visual receipts bind checker_sources or producer_sources'
NATIVE_POLICY = 'answer_selection_policy source_sha256 is in the frozen native route baseline'
# Editing these moves a hash that frozen evidence compares with the current source.
# The count is the number of calls left implicit, so the exemption cannot grow unseen.
HASH_BOUND = {
    'src/harness/lexbench/charts.py': (2, PUBLISHED_COPY),
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
TEMPORARY_FILES = {'NamedTemporaryFile', 'TemporaryFile', 'SpooledTemporaryFile'}
# `.open` on these names is not a file-object constructor with an encoding parameter.
OTHER_OPENERS = {'os', 'tarfile', 'Image', 'zipfile', 'gzip', 'webbrowser', 'opener'}


def _is_text_mode(mode):
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and 'b' not in mode.value


def _mode(call, position):
    for keyword in call.keywords:
        if keyword.arg == 'mode':
            return keyword.value
    return call.args[position] if len(call.args) > position else None


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
    if name in TEMPORARY_FILES:
        return _is_text_mode(_mode(call, 0))
    if name == 'fdopen' or (name == 'open' and receiver is None):
        mode = _mode(call, 1)
        return mode is None or _is_text_mode(mode)
    if name == 'open' and receiver.split('.')[-1] not in OTHER_OPENERS and 'build_opener' not in receiver:
        return not (call.args or call.keywords) or _is_text_mode(_mode(call, 0))
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
    'os.fdopen(descriptor, "wb")\n',
    'os.open(path, os.O_RDONLY)\n',
    'tempfile.NamedTemporaryFile(dir=parent)\n',
    'tempfile.TemporaryFile()\n',
    'tarfile.open(fileobj=stream, mode="r:gz")\n',
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
