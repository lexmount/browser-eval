"""Startup diagnostics belong to the first call, even when scheduling varies."""

import sys
import time

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider


@pytest.mark.parametrize('outcome', [
    'success', 'failure', 'exit', 'timeout', 'startup-exit', 'closed-input',
])
def test_first_call_retains_startup_diagnostics(tmp_path, monkeypatch, outcome):
    script = (
        'import json,sys,time,os\n'
        + ('os.close(0)\n' if outcome == 'closed-input' else '')
        + 'print("startup diagnostic",file=sys.stderr,flush=True)\n'
        + ('sys.exit(9)\n' if outcome == 'startup-exit' else '')
        + ('time.sleep(10)\n' if outcome == 'closed-input' else '')
        + 'for line in sys.stdin:\n'
        + {'success': ' print(json.dumps({"ok":True,"content":"ready"}),flush=True)\n',
           'failure': (' print(json.dumps({"ok":False,"error_message":"tool rejected"}),'
                       'flush=True)\n'),
           'exit': ' sys.exit(7)\n',
           'timeout': ' time.sleep(10)\n',
           'startup-exit': ' pass\n', 'closed-input': ' pass\n'}[outcome]
    )
    provider = SubprocessJsonlProvider('fixture', {
        'repo_path': str(tmp_path), 'command': [sys.executable, '-u', '-c', script],
    })
    start = provider._start

    def startup_before_request():
        process = start()
        # Force the legal schedule where startup stderr is read before fetch
        # can send its first request; no sleep-based race or upstream service.
        deadline = time.monotonic() + 2
        with provider._stderr_lock:
            while provider._stderr_sequence == 0:
                remaining = deadline - time.monotonic()
                assert remaining > 0, 'fixture startup did not complete'
                provider._stderr_lock.wait(timeout=remaining)
        if outcome == 'startup-exit':
            process.wait(timeout=2)
        return process

    monkeypatch.setattr(provider, '_start', startup_before_request)
    case = EvalCase('one', 'One', 'https://example.invalid', 'static', [])
    try:
        if outcome in ('exit', 'timeout', 'startup-exit', 'closed-input'):
            for _ in range(2):
                error_type = {
                    'exit': RuntimeError, 'timeout': TimeoutError,
                    'startup-exit': BrokenPipeError, 'closed-input': BrokenPipeError,
                }[outcome]
                with pytest.raises(error_type) as error:
                    provider.fetch(case, .5)
                assert error.value.public_metadata['stderr'] == 'startup diagnostic\n'
                assert error.value.public_metadata['stderr_bytes'] == len('startup diagnostic\n')
                assert not error.value.public_metadata['stderr_truncated']
                if outcome == 'startup-exit':
                    assert error.value.public_metadata['exit_code'] == 9
                if outcome == 'closed-input':
                    assert provider._process.poll() is not None
        else:
            first = provider.fetch(case, 2)
            second = provider.fetch(case, 2)
            assert first.ok is (outcome == 'success')
            assert first.metadata['stderr'] == 'startup diagnostic\n'
            assert first.metadata['stderr_bytes'] == len('startup diagnostic\n')
            assert not first.metadata['stderr_truncated']
            assert second.metadata['stderr'] == ''
            assert second.metadata['stderr_bytes'] == 0
    finally:
        provider.__del__()
