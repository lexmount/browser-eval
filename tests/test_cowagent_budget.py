import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider


def test_uniform_timeout_reaches_native_tool_and_raw_envelope_is_retained(tmp_path):
    calls = []
    module = SimpleNamespace(DEFAULT_TIMEOUT=30)
    class WebFetch:
        def __init__(self, config):
            pass
        def execute(self, arguments):
            calls.append((arguments, module.DEFAULT_TIMEOUT))
            return SimpleNamespace(status='success', result='native answer',
                                   ext_data={'native_field': 1}, display='native display')
    module.WebFetch = WebFetch
    provider = CowAgentBuiltinProvider('cow', {'repo_path': str(tmp_path), 'workspace': str(tmp_path)})
    with patch('harness.web_fetch.providers.cowagent.importlib.import_module', return_value=module):
        result = provider.fetch(EvalCase('one', 'one', 'https://example.com/', 'control', []), 45)
    assert calls == [({'url': 'https://example.com/'}, 45)]
    assert module.DEFAULT_TIMEOUT == 30
    assert result.content == 'native answer'
    assert result.metadata['effective_timeout_seconds'] == 45
    from harness.web_fetch.runner import persist_result_artifacts
    persist_result_artifacts(tmp_path, Path('artifacts/cow/control.txt'), result)
    raw = json.loads((tmp_path / result.metadata['raw_response_path']).read_bytes())
    assert raw == {'status': 'success', 'result': 'native answer', 'ext_data': {'native_field': 1}, 'display': 'native display'}


def test_native_default_restored_when_tool_raises(tmp_path):
    module = SimpleNamespace(DEFAULT_TIMEOUT=30)
    class WebFetch:
        def __init__(self, config):
            pass
        def execute(self, arguments):
            assert module.DEFAULT_TIMEOUT == 45
            raise RuntimeError('native failure')
    module.WebFetch = WebFetch
    provider = CowAgentBuiltinProvider('cow', {'repo_path': str(tmp_path), 'workspace': str(tmp_path)})
    with patch('harness.web_fetch.providers.cowagent.importlib.import_module', return_value=module):
        with pytest.raises(RuntimeError, match='native failure'):
            provider.fetch(EvalCase('one', 'one', 'https://example.com/', 'control', []), 45)
    assert module.DEFAULT_TIMEOUT == 30
