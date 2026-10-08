"""Installed distributions must match source modules and declared package data."""
import shutil

import pytest

from tests.package_smoke import verify_package_tree


@pytest.fixture
def package(tmp_path):
    source, installed = tmp_path / 'source', tmp_path / 'installed'
    (source / 'harness/example/data').mkdir(parents=True)
    (source / 'harness/example/__init__.py').write_text('VALUE = 1\n')
    (source / 'harness/example/data/tasks.json').write_text('{"task":"synthetic"}\n')
    (source / 'pyproject.toml').write_text(
        '[tool.setuptools.package-data]\n"harness.example" = ["data/*.json"]\n')
    shutil.copytree(source / 'harness', installed)
    return source, installed


def test_current_modules_and_declared_assets_are_accepted(package):
    source, installed = package
    (installed / 'example/__pycache__').mkdir()
    (installed / 'example/__pycache__/__init__.cpython-311.pyc').write_bytes(b'local bytecode')
    verify_package_tree(source, installed)


@pytest.mark.parametrize('damage', ['missing-module', 'missing-asset', 'changed-module',
                                   'changed-asset', 'retired-module', 'unbound-asset',
                                   'linked-module', 'linked-root'])
def test_stale_missing_changed_or_linked_package_is_rejected(package, damage, tmp_path):
    source, installed = package
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
        verify_package_tree(source, installed)
