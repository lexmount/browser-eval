"""Checkout entrypoints must beat a noneditable installed Browser Eval wheel."""
import csv
import io
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted(path for path in (ROOT/'scripts').glob('*.py')
                 if 'sys.path.' in path.read_text())


@pytest.fixture(scope='module')
def stale_wheel_install(tmp_path_factory):
    root = tmp_path_factory.mktemp('stale-browser-eval-wheel')
    wheel = root/'browser_harness_eval-0.0.1-py3-none-any.whl'
    info = 'browser_harness_eval-0.0.1.dist-info'
    files = {
        'harness/distribution.py': b'STALE_INSTALLED_CODE = True\n',
        'lexbench_webfetch/__init__.py': b'STALE_INSTALLED_CODE = True\n',
        'lexbench_websearch/__init__.py': b'STALE_INSTALLED_CODE = True\n',
        info+'/METADATA': b'Metadata-Version: 2.1\nName: browser-harness-eval\nVersion: 0.0.1\n',
        info+'/WHEEL': b'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    record = io.StringIO()
    writer = csv.writer(record)
    for name in [*files, info+'/RECORD']:
        writer.writerow([name, '', ''])
    files[info+'/RECORD'] = record.getvalue().encode()
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    target = root/'site-packages'
    subprocess.run([shutil.which('uv'), 'pip', 'install', '--no-index', '--no-deps',
                    '--target', str(target), str(wheel)], check=True, capture_output=True)
    return target


PROBE = r'''
import ast, importlib, pathlib, sys
script = pathlib.Path(sys.argv[1])
prefix = []
for node in ast.parse(script.read_text()).body:
    prefix.append(node)
    if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and ast.unparse(node.value.func.value) == 'sys.path'):
        break
else:
    raise AssertionError('Entrypoint has no import bootstrap')
exec(compile(ast.Module(body=prefix, type_ignores=[]), str(script), 'exec'),
     {'__file__': str(script), '__name__': 'entrypoint_import_probe'})
checkout = script.parent.parent/'src'
for name in ('harness.distribution', 'lexbench_webfetch', 'lexbench_websearch'):
    module = importlib.import_module(name)
    origin = pathlib.Path(module.__file__).resolve()
    assert origin.is_relative_to(checkout), (script.name, name, str(origin))
    assert not getattr(module, 'STALE_INSTALLED_CODE', False)
'''


@pytest.mark.parametrize('script', SCRIPTS, ids=lambda path: path.name)
def test_entrypoint_prioritizes_checkout_over_noneditable_wheel(
    script, stale_wheel_install, tmp_path
):
    environment = {**os.environ, 'PYTHONPATH': str(stale_wheel_install)}
    # -S disables editable-install .pth hooks, which would otherwise hide the bug.
    result = subprocess.run([sys.executable, '-S', '-c', PROBE, str(script)],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('name', ['assemble_headless.py', 'build_search_report.py'])
def test_complete_cli_help_works_without_editable_install(
    name, stale_wheel_install, tmp_path
):
    import site
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join(
        [str(stale_wheel_install), *site.getsitepackages()])}
    # These help commands only parse arguments; they do not collect or render results.
    result = subprocess.run([sys.executable, '-S', str(ROOT/'scripts'/name), '--help'],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout.lower()
