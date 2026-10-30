"""Acceptance dependencies must invalidate the source receipt when changed."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import lexbench_webfetch
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256


@pytest.mark.parametrize('changed', ['dynamic_reference.py', 'file_acceptance.py',
                                     'evaluation.py', 'models.py', 'paths.py'])
def test_acceptance_dependency_change_invalidates_source_identity(tmp_path, changed):
    package = tmp_path/'lexbench_webfetch'
    package.mkdir()
    for source in Path(lexbench_webfetch.__file__).parent.glob('*.py'):
        shutil.copyfile(source, package/source.name)
    target = package/changed
    target.write_bytes(target.read_bytes()+b'\n# Synthetic source identity control.\n')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run([sys.executable, '-c',
                             'from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256; print(SCORING_SOURCE_SHA256)'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() != SCORING_SOURCE_SHA256
