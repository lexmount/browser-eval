"""Installed distributions must match source modules and declared package data."""
import shutil

import pytest

from tests.package_smoke import verify_dependency_metadata, verify_package_tree


@pytest.mark.parametrize('installed', ['correct', 'registry-version', 'wrong-fork'])
def test_installed_optional_dependency_preserves_declared_source(tmp_path, monkeypatch, installed):
    import importlib.metadata

    expected = 'webarena-verified @ git+https://example.invalid/evaluator.git@' + 'a' * 40
    (tmp_path / 'pyproject.toml').write_text(
        '[project.optional-dependencies]\nbrowser-use-eval = ["' + expected + '"]\n')
    requirement = expected if installed == 'correct' else (
        'webarena-verified==1.2.3' if installed == 'registry-version'
        else expected.replace('evaluator.git', 'other.git'))
    monkeypatch.setattr(importlib.metadata, 'requires', lambda name: [
        requirement + '; extra == "browser-use-eval"'])
    if installed == 'correct':
        verify_dependency_metadata(tmp_path)
    else:
        with pytest.raises(ValueError, match='dependency differs'):
            verify_dependency_metadata(tmp_path)


@pytest.fixture(params=['harness', 'lexbench_webfetch', 'lexbench_websearch'])
def package(tmp_path, request):
    source, installed = tmp_path / 'source', tmp_path / 'installed'
    name = request.param
    directory = 'src/' + name
    (source / directory / 'example/data').mkdir(parents=True)
    (source / directory / 'example/__init__.py').write_text('VALUE = 1\n')
    (source / directory / 'example/data/tasks.json').write_text('{"task":"synthetic"}\n')
    (source / 'pyproject.toml').write_text(
        '[tool.setuptools.package-dir]\n' + name + ' = "' + directory + '"\n'
        '[tool.setuptools.package-data]\n"' + name + '.example" = ["data/*.json"]\n')
    shutil.copytree(source / directory, installed)
    return source, installed, name


def test_current_modules_and_declared_assets_are_accepted(package):
    source, installed, name = package
    (installed / 'example/__pycache__').mkdir()
    (installed / 'example/__pycache__/__init__.cpython-311.pyc').write_bytes(b'local bytecode')
    verify_package_tree(source, installed, package_name=name)


@pytest.mark.parametrize('damage', ['missing-module', 'missing-asset', 'changed-module',
                                   'changed-asset', 'retired-module', 'unbound-asset',
                                   'linked-module', 'linked-root'])
def test_stale_missing_changed_or_linked_package_is_rejected(package, damage, tmp_path):
    source, installed, name = package
    module = installed / 'example/__init__.py'
    asset = installed / 'example/data/tasks.json'
    if damage == 'missing-module':
        module.unlink()
    elif damage == 'missing-asset':
        asset.unlink()
    elif damage == 'changed-module':
        module.write_text('VALUE = 2\n')
    elif damage == 'changed-asset':
        asset.write_text('{"task":"wrong"}\n')
    elif damage == 'retired-module':
        (installed / 'example/retired.py').write_text('VALUE = 0\n')
    elif damage == 'unbound-asset':
        (installed / 'example/data/private-note.txt').write_text('synthetic private note')
    elif damage == 'linked-module':
        actual = tmp_path / 'same-module.py'
        actual.write_bytes(module.read_bytes())
        module.unlink()
        module.symlink_to(actual)
    else:
        linked = tmp_path / 'linked-package'
        linked.symlink_to(installed, target_is_directory=True)
        installed = linked
    with pytest.raises(ValueError):
        verify_package_tree(source, installed, package_name=name)
