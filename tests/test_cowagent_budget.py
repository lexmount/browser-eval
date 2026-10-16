"""Native timers and tool responses survive the isolated worker boundary."""
import json
from pathlib import Path

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider
from harness.web_fetch.runner import persist_result_artifacts


def test_native_timer_and_response_survive_success_failure_and_next_call(tmp_path):
    package = tmp_path / 'agent/tools/web_fetch'
    package.mkdir(parents=True)
    for directory in (tmp_path / 'agent', tmp_path / 'agent/tools', package):
        (directory / '__init__.py').write_text('')
    (package / 'web_fetch.py').write_text('''from types import SimpleNamespace
DEFAULT_TIMEOUT = 30
class WebFetch:
    def __init__(self, config): pass
    def execute(self, arguments):
        assert DEFAULT_TIMEOUT == 30
        if arguments['url'].endswith('/error'):
            raise RuntimeError('native failure')
        return SimpleNamespace(status='success', result='native answer',
                               ext_data={'native_field': 1}, display='native display')
''')
    provider = CowAgentBuiltinProvider('cow', {'repo_path': str(tmp_path),
                                             'workspace': str(tmp_path / 'workspace')})
    try:
        for suffix in ('success', 'error', 'next'):
            answer = provider.fetch(EvalCase(suffix, suffix, 'https://example.com/' + suffix,
                                            'control', []), 45)
            assert answer.metadata['cowagent_default_timeout_seconds'] == 30
            if suffix == 'error':
                assert not answer.ok
                assert answer.error_type == 'RuntimeError'
                assert answer.error_message == 'native failure'
                continue
            assert answer.ok and answer.content == 'native answer'
            assert answer.metadata['effective_timeout_seconds'] == 30
            persist_result_artifacts(tmp_path, Path('artifacts/cow/' + suffix + '.txt'), answer)
            envelope = json.loads((tmp_path / answer.metadata['raw_response_path']).read_bytes())
            assert envelope['raw_tool_response'] == {
                'status': 'success', 'result': 'native answer',
                'ext_data': {'native_field': 1}, 'display': 'native display',
            }
    finally:
        provider.__del__()
