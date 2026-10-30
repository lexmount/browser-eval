"""Selecting editions works without optional publication dependencies."""
import subprocess
import sys


def test_pinning_import_and_help_work_without_yaml_or_hub(tmp_path):
    program = '''
import importlib.abc
import runpy
import sys

class BaseInstallation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'yaml', 'huggingface_hub'}:
            raise ModuleNotFoundError('Optional publication dependency unavailable: ' + fullname)

sys.meta_path.insert(0, BaseInstallation())
from harness.benchmarks.pinning import validate_selected_binding
assert validate_selected_binding({'revision': 'a' * 40}) == 'a' * 40
sys.argv = ['dataset-pin', '--help']
runpy.run_module('harness.benchmarks.pinning', run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '--revision' in result.stdout
