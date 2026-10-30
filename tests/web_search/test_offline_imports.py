"""Help and imports never acquire benchmark data or require a warm cache."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("operation", [
    "import lexbench_websearch.rubric; import harness.web_search.relevance; import harness.web_search.review",
    "from harness.web_search.relevance import judge_main; judge_main(['--help'])",
])
def test_rubric_import_and_judge_help_are_cold_offline(tmp_path, operation):
    code = '''import socket, urllib.request

def forbidden(*args, **kwargs):
    raise AssertionError("Help/import attempted network access")
socket.socket.connect = forbidden
urllib.request.urlopen = forbidden
''' + operation
    env = {**os.environ, "BROWSER_EVAL_DATASET_CACHE": str(tmp_path / "empty")}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "empty").rglob("*"))
