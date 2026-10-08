"""Both browser procedures can use the environment without loading either runner."""
import subprocess
import sys


def test_environment_and_network_evidence_do_not_load_an_agent_or_replay_engine():
    program = '''
import importlib.abc
import sys
class BlockTrackImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('harness.browser_use', 'harness.trajectory_replay')):
            raise AssertionError('Environment loaded a track: ' + fullname)
sys.meta_path.insert(0, BlockTrackImports())
from harness.environments.webarena.runtime import (
    browser_tool_config, browser_session_contract, configure_benchmark,
    PHP_FPM_COMPAT_CONFIG,
)
from harness.environments.webarena.har import CdpHarRecorder
config = browser_tool_config('http://127.0.0.1:9222')
assert config['cdp_isolated_context'] is True
assert browser_session_contract()['viewport'] == {'width': 1280, 'height': 900}
assert configure_benchmark().config.environments
assert PHP_FPM_COMPAT_CONFIG.is_file()
'''
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
